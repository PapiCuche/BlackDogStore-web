import { API_BASE } from "./api";
import { fetchWithAuth } from "./auth";

/**
 * «Mi cuenta» — the signed-in buyer's own profile in this shop (CLIENT-SEC-01).
 *
 * The shop is the one whose page is open: the server reads it from the host and
 * nothing here names one. The e-mail is the account's and is only shown.
 */

export type DocumentType = "" | "dni" | "ruc" | "ce";

export type AccountProfile = {
  email: string;
  /** When the mailbox was PROVEN, or null: unknown is not verified. */
  email_verified_at: string | null;
  /** Whether this account has an address nobody proved yet and can prove it now. The server decides. */
  email_verification_available: boolean;
  first_name: string;
  last_name: string;
  phone: string;
  document_type: DocumentType;
  document_number: string;
  /** The document is the shop's to change: the form shows it and does not offer it. The server decides. */
  document_locked?: boolean;
};

export type ProfileFields = Pick<
  AccountProfile,
  "first_name" | "last_name" | "phone" | "document_type" | "document_number"
>;

export const PROFILE_FIELDS: (keyof ProfileFields)[] = [
  "first_name", "last_name", "phone", "document_type", "document_number",
];

/** A save the server refused: `fields` is what it said, field by field. */
export class ProfileError extends Error {
  readonly status: number;
  readonly fields: Partial<Record<keyof ProfileFields, string>>;

  constructor(message: string, status: number, fields: Partial<Record<keyof ProfileFields, string>> = {}) {
    super(message);
    this.name = "ProfileError";
    this.status = status;
    this.fields = fields;
  }
}

const URL = `${API_BASE}/account/profile/`;

export async function fetchProfile(): Promise<AccountProfile> {
  const res = await fetchWithAuth(URL);
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ProfileError(body?.detail || "No se pudo cargar tu perfil.", res.status);
  return body as AccountProfile;
}

function fieldErrors(body: unknown): Partial<Record<keyof ProfileFields, string>> {
  const found: Partial<Record<keyof ProfileFields, string>> = {};
  if (!body || typeof body !== "object") return found;
  for (const field of PROFILE_FIELDS) {
    const said = (body as Record<string, unknown>)[field];
    if (Array.isArray(said) && said.length > 0) found[field] = String(said[0]);
    else if (typeof said === "string" && said) found[field] = said;
  }
  return found;
}

/** Sends ONLY what changed: a field that is not sent is not touched. */
export async function saveProfile(changes: Partial<ProfileFields>): Promise<AccountProfile> {
  const res = await fetchWithAuth(URL, { method: "PATCH", body: JSON.stringify(changes) });
  const body = await res.json().catch(() => null);
  if (res.ok) return body as AccountProfile;
  if (res.status === 429) {
    throw new ProfileError("Guardaste tu perfil muchas veces seguidas. Inténtalo de nuevo más tarde.", 429);
  }
  const fields = fieldErrors(body);
  throw new ProfileError(
    body?.detail || (Object.keys(fields).length > 0 ? "Revisa los datos marcados." : "No se pudo guardar tu perfil."),
    res.status,
    fields,
  );
}

// ── Proving my own mailbox (CLIENT-SEC-01 · F7) ─────────────────────────────

/**
 * Ask for the 6-digit code. It goes to the account's OWN address: nothing sent
 * from here says where. `true` when a code is on its way, `false` when the
 * address was already verified.
 */
export async function sendVerificationCode(): Promise<boolean> {
  const res = await fetchWithAuth(`${API_BASE}/account/verify-email/send/`, { method: "POST", body: "{}" });
  const body = await res.json().catch(() => null);
  if (res.ok) return !body?.already_verified;
  if (res.status === 429) {
    // The server's own sentence when it has one; never the framework's English.
    const said = typeof body?.detail === "string" && !/throttled/i.test(body.detail) ? body.detail : "";
    throw new ProfileError(said || "Pediste un código hace muy poco. Espera un minuto antes de pedir otro.", 429);
  }
  throw new ProfileError(body?.detail || "No pudimos enviar el código. Inténtalo de nuevo.", res.status);
}

/** Type the code back. Answers when the address was proved. */
export async function confirmVerificationCode(code: string): Promise<string> {
  const res = await fetchWithAuth(`${API_BASE}/account/verify-email/confirm/`, {
    method: "POST",
    body: JSON.stringify({ code }),
  });
  const body = await res.json().catch(() => null);
  if (res.ok && body?.email_verified_at) return body.email_verified_at as string;
  if (res.status === 429) throw new ProfileError("Demasiados intentos. Espera un minuto y vuelve a probar.", 429);
  throw new ProfileError(body?.detail || "El código no es válido o ya venció. Pide uno nuevo.", res.status);
}
