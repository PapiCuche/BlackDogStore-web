/**
 * WHATSAPP-NOTIFY — the part of a company's messaging configuration its
 * administrator may see and change.
 *
 * NO CREDENTIAL IS IN THIS FILE'S TYPES, BY DESIGN. The server reports whether
 * each one resolves, as a boolean. They are set by whoever operates the
 * installation, outside this app, and never travel to a browser.
 */

import { API_BASE } from "./api";
import { fetchWithAuth } from "./auth";

export type WhatsAppSettingsData = {
  enabled: boolean;
  provider: string;
  ready: boolean;
  missing: string[];
  phone_number_configured: boolean;
  credentials: { access_token: boolean; app_secret: boolean; verify_token: boolean };
  default_calling_code: string;
  template_language: string;
  templates: Record<string, string>;
  events: { code: string; label: string }[];
  template_parameters: string[];
  webhook_path: string;
};

const url = (slug: string) =>
  `${API_BASE}/v1/internal/${encodeURIComponent(slug)}/messaging/whatsapp/`;

async function read(res: Response): Promise<WhatsAppSettingsData> {
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(
      (body && typeof body === "object" && "detail" in body ? String(body.detail) : "") ||
        "No se pudo consultar la configuración de WhatsApp.",
    );
  }
  return body as WhatsAppSettingsData;
}

export const fetchWhatsAppSettings = async (slug: string) => read(await fetchWithAuth(url(slug)));

export const updateWhatsAppSettings = async (
  slug: string,
  body: Partial<{
    enabled: boolean;
    templates: Record<string, string>;
    default_calling_code: string;
    template_language: string;
  }>,
) => read(await fetchWithAuth(url(slug), { method: "PATCH", body: JSON.stringify(body) }));
