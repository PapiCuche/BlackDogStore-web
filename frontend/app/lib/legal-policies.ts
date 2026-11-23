/**
 * LEGAL-POLICIES-01 — the published legal documents of THIS storefront's company.
 *
 * Fetched on the server like `fetchStorefrontConfig`: the backend resolves the
 * company from the request host, so the page always shows the text of the shop
 * that serves it — never a hardcoded one. `null` means «not published» (or not
 * reachable): the page then shows its controlled fallback.
 */
import { API_BASE } from "./api";

export type LegalKind = "privacy" | "cookies" | "whatsapp";

export type PublishedLegalPolicy = {
  kind: LegalKind;
  text: string;
  published_at: string | null;
};

/**
 * `title` is the short name used in links and tabs; `heading` (when present) is the
 * page's own title. The «whatsapp» kind keeps its key and its address
 * `/privacy/whatsapp` (Meta may already reference it) but covers every messaging
 * channel the CRM answers: WhatsApp, Instagram Direct and Facebook Messenger.
 */
export const LEGAL_PAGES: Record<LegalKind, { title: string; href: string; heading?: string }> = {
  privacy: { title: "Política de privacidad", href: "/privacy" },
  cookies: { title: "Política de cookies", href: "/cookies" },
  whatsapp: {
    title: "Privacidad de mensajería y CRM",
    heading: "Privacidad de mensajería (WhatsApp, Instagram y Messenger) y CRM",
    href: "/privacy/whatsapp",
  },
};

/** The page title of a legal document: its `heading`, or its short `title`. */
export function legalHeading(kind: LegalKind): string {
  return LEGAL_PAGES[kind].heading ?? LEGAL_PAGES[kind].title;
}

export async function fetchLegalPolicy(kind: LegalKind): Promise<PublishedLegalPolicy | null> {
  try {
    const res = await fetch(`${API_BASE}/storefront/legal/${kind}/`, {
      cache: "no-store",
      // A hung backend must not hang the legal page: it falls back instead.
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as PublishedLegalPolicy;
    return typeof data?.text === "string" && data.text.trim() ? data : null;
  } catch {
    return null;
  }
}
