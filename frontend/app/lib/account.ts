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
  /** Whether «send the verification again» would do anything for this account. The server decides. */
  email_verification_available: boolean;
  first_name: string;
  last_name: string;
  phone: string;
  document_type: DocumentType;
  document_number: string;
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
