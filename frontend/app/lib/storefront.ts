/**
 * Storefront identity and branding — Phase 3.
 *
 * The public shop stops knowing which business it is. It asks
 * `/api/storefront/config/`, which resolves the tenant from the REQUEST HOST —
 * the same rule the catalogue and cart already use — and returns that company's
 * name, logo, palette, contact details and policies.
 *
 * Fetched on the SERVER, in the root layout, for two reasons: the page renders
 * with the right name and colours on first paint instead of flashing a neutral
 * theme, and the metadata (title, description, OpenGraph) can carry the tenant's
 * name, which a client-side fetch is too late to do.
 *
 * When the config cannot be fetched — the backend is down, the host resolves to
 * nothing — the page falls back to the NEUTRAL platform theme and a shop with no
 * name. It never falls back to a specific business: an unbranded page is a
 * visible problem, one wearing the wrong company's identity is not.
 */

import { API_BASE } from "./api";

export type StorefrontLogos = {
  primary_on_light: string;
  primary_on_dark: string;
  horizontal_on_light: string;
  horizontal_on_dark: string;
  /**
   * M12F — el isotipo, recurso auxiliar autorizado por el manual.
   *
   * Existe como variante propia porque en 320 px un lockup horizontal no cabe
   * junto al carrito, el tema y el menú — y la salida no es encogerlo por
   * debajo de su mínimo ni aplastarlo, que son alteraciones prohibidas, sino
   * usar la pieza que el manual ya diseñó para ese tamaño.
   */
  isotype_on_light: string;
  isotype_on_dark: string;
};

export const EMPTY_LOGOS: StorefrontLogos = {
  primary_on_light: "",
  primary_on_dark: "",
  horizontal_on_light: "",
  horizontal_on_dark: "",
  isotype_on_light: "",
  isotype_on_dark: "",
};

/**
 * M12F — contenido comercial del tenant, editable sin desplegar.
 *
 * Todo puede venir vacío, y vacío es una respuesta legítima: significa «este
 * tenant no ha escrito esto» y el componente cae a un texto genérico de la
 * plataforma. NUNCA al de otra empresa.
 */
export type StorefrontCampaign = {
  slot: string;
  badge: string;
  title: string;
  subtitle: string;
  body: string;
  image_url: string;
  cta_label: string;
  cta_url: string;
  secondary_cta_label: string;
  secondary_cta_url: string;
  product: { slug: string; name: string } | null;
};

export type StorefrontPage = {
  hero_eyebrow: string;
  hero_title: string;
  hero_subtitle: string;
  hero_primary_cta_label: string;
  hero_primary_cta_url: string;
  hero_secondary_cta_label: string;
  hero_secondary_cta_url: string;
};

export const EMPTY_PAGE: StorefrontPage = {
  hero_eyebrow: "",
  hero_title: "",
  hero_subtitle: "",
  hero_primary_cta_label: "",
  hero_primary_cta_url: "",
  hero_secondary_cta_label: "",
  hero_secondary_cta_url: "",
};

export type StorefrontColors = {
  primary_color: string;
  accent_color: string;
  background_color: string;
  surface_color: string;
  text_color: string;
  border_color: string;
};

export type StorefrontConfig = {
  company: {
    name: string;
    slug: string;
    legal_name: string;
    tax_id: string;
  };
  branding: {
    /** Legado. Sigue existiendo: hay tenants que sólo tienen éste. */
    logo_url: string;
    /**
     * M12E — variantes por contraste.
     *
     * Las claves son PREGUNTAS que hace un componente («horizontal, sobre
     * oscuro»), no nombres de columna. Cadena vacía significa «no tengo esa
     * variante», y es una respuesta legítima: quien la consume cae al nombre de
     * la empresa antes que dibujar un logo ilegible.
     */
    logos: StorefrontLogos;
    colors: StorefrontColors;
    /** `{"--brand-primary": "#FFFFFF", ...}` — already validated server-side. */
    css_variables: Record<string, string>;
  };
  contact: {
    email: string;
    phone: string;
    whatsapp_number: string;
    whatsapp_link: string;
    website_url: string;
    facebook_url: string;
    instagram_url: string;
    address: string;
    city: string;
  };
  policies: {
    warranty_text: string;
    warranty_url: string;
    terms_url: string;
    privacy_url: string;
  };
  /** M12F — contenido estable de la portada. */
  page: StorefrontPage;
  /**
   * M12F — campañas vigentes, indexadas por slot.
   *
   * Un diccionario y no una lista: la página pregunta «¿qué va en la promoción
   * inferior?» y recibe una respuesta o nada. Sólo llega lo PUBLICADO y dentro
   * de su ventana — un borrador no viaja hasta aquí, y una caducada tampoco.
   */
  campaigns: Record<string, StorefrontCampaign | undefined>;
};

/**
 * The neutral platform theme, mirroring `company_settings.NEUTRAL_THEME`.
 *
 * Used only when the config cannot be fetched. Deliberately belongs to no
 * business: a dark, unbranded surface.
 */
export const NEUTRAL_CONFIG: StorefrontConfig = {
  company: { name: "", slug: "", legal_name: "", tax_id: "" },
  branding: {
    logo_url: "",
    logos: { ...EMPTY_LOGOS },
    colors: {
      primary_color: "#FFFFFF",
      accent_color: "#A1A1AA",
      background_color: "#0A0A0A",
      surface_color: "#141414",
      text_color: "#FAFAFA",
      border_color: "#262626",
    },
    css_variables: {
      // Esta lista es la ALLOWLIST de `brandingStyle()`: una variable que no
      // esté aquí se descarta antes de llegar al atributo `style`. Añadir un
      // token al backend sin añadirlo aquí produce exactamente el síntoma que
      // tuvo M12E — el backend lo manda y la página no lo ve.
      "--brand-primary": "#FFFFFF",
      "--brand-accent": "#A1A1AA",
      "--brand-light-background": "#FFFFFF",
      "--brand-light-surface": "#F4F4F5",
      "--brand-background": "#0A0A0A",
      "--brand-surface": "#141414",
      "--brand-text": "#FAFAFA",
      "--brand-border": "#262626",
    },
  },
  contact: {
    email: "",
    phone: "",
    whatsapp_number: "",
    whatsapp_link: "",
    website_url: "",
    facebook_url: "",
    instagram_url: "",
    address: "",
    city: "",
  },
  policies: { warranty_text: "", warranty_url: "", terms_url: "", privacy_url: "" },
  page: { ...EMPTY_PAGE },
  // Sin campañas. Una plataforma sin tenant resuelto no anuncia nada de nadie.
  campaigns: {},
};

/** Only `#RRGGBB` reaches a stylesheet. The backend validates; so does this. */
const HEX = /^#[0-9A-Fa-f]{6}$/;

/**
 * Turn the config's CSS variables into a style string for the document root.
 *
 * DEFENCE IN DEPTH, not paranoia about our own API: this string is interpolated
 * into a `style` attribute, so it is the last point at which a malformed value
 * could escape the declaration. Anything that is not six hex digits is dropped,
 * and the variable name has to be one we asked for.
 */
export function brandingStyle(config: StorefrontConfig): Record<string, string> {
  const allowed = new Set(Object.keys(NEUTRAL_CONFIG.branding.css_variables));
  const style: Record<string, string> = {};
  for (const [name, value] of Object.entries(config.branding.css_variables ?? {})) {
    if (allowed.has(name) && HEX.test(value)) style[name] = value;
  }
  return style;
}

/**
 * Fetch the storefront config. Never throws — a shop must render.
 *
 * `no-store` because the tenant depends on the request host: a cached response
 * shared across hosts is the same bug as a cache key without a tenant, one layer
 * up. The backend sets a short `Cache-Control` with `Vary: Host` for shared
 * caches that key correctly.
 */
export async function fetchStorefrontConfig(): Promise<StorefrontConfig> {
  try {
    const res = await fetch(`${API_BASE}/storefront/config/`, { cache: "no-store" });
    if (!res.ok) return NEUTRAL_CONFIG;
    const data = (await res.json()) as StorefrontConfig;
    return {
      ...NEUTRAL_CONFIG,
      ...data,
      branding: {
        ...NEUTRAL_CONFIG.branding,
        ...(data.branding ?? {}),
        // Un nivel más de mezcla: si el backend manda `logos` parcial —o no lo
        // manda porque es una versión anterior— las claves que falten quedan
        // vacías en vez de `undefined`, y `undefined` es lo que rompe un
        // `logos.horizontal_on_dark` aguas abajo.
        logos: { ...EMPTY_LOGOS, ...(data.branding?.logos ?? {}) },
      },
      contact: { ...NEUTRAL_CONFIG.contact, ...(data.contact ?? {}) },
      policies: { ...NEUTRAL_CONFIG.policies, ...(data.policies ?? {}) },
      company: { ...NEUTRAL_CONFIG.company, ...(data.company ?? {}) },
      // Mismo motivo que `logos`: un backend anterior a M12F no manda estas
      // claves, y la portada tiene que renderizar igual.
      page: { ...EMPTY_PAGE, ...(data.page ?? {}) },
      campaigns: data.campaigns ?? {},
    };
  } catch {
    // The shop renders unbranded rather than not at all.
    return NEUTRAL_CONFIG;
  }
}
