import { API_BASE } from "./api";
import { fetchWithAuth } from "./auth";

/**
 * «Mis direcciones» — the signed-in buyer's address book in this shop (DELIVERY-01).
 *
 * The shop is the one whose page is open; the book is the account's. Nothing
 * here names either: the server reads both from the request.
 */

export type AddressKind = "address" | "agency";

export type SavedAddress = {
  id: number;
  label: string;
  kind: AddressKind;
  recipient_name: string;
  recipient_document_type: "" | "dni" | "ruc" | "ce";
  recipient_document_number: string;
  recipient_phone: string;
  address_line: string;
  district: string;
  city: string;
  department: string;
  reference: string;
  /** For an agency: one of the shop's carriers, and its name. */
  carrier: number | null;
  carrier_name: string;
  agency_name: string;
  agency_locality: string;
  is_default: boolean;
};

export type AddressDraft = Omit<SavedAddress, "id" | "carrier_name" | "is_default">;

export type AddressBook = {
  results: SavedAddress[];
  /** How many active entries a book may hold. */
  limit: number;
  /** The shop's agencies a buyer can choose from. */
  agency_carriers: { id: number; name: string }[];
};

/** A write the server refused: `fields` is what it said, field by field. */
export class AddressError extends Error {
  readonly status: number;
  readonly fields: Record<string, string>;

  constructor(message: string, status: number, fields: Record<string, string> = {}) {
    super(message);
    this.name = "AddressError";
    this.status = status;
    this.fields = fields;
  }
}

const URL = `${API_BASE}/account/addresses/`;

async function refusal(res: Response, fallback: string): Promise<never> {
  const body = await res.json().catch(() => null);
  if (res.status === 429) throw new AddressError("Hiciste muchos cambios seguidos. Inténtalo de nuevo más tarde.", 429);
  const fields: Record<string, string> = {};
  if (body && typeof body === "object") {
    for (const [key, said] of Object.entries(body as Record<string, unknown>)) {
      if (key === "detail") continue;
      if (Array.isArray(said) && said.length > 0) fields[key] = String(said[0]);
      else if (typeof said === "string" && said) fields[key] = said;
    }
  }
  const detail = body && typeof (body as { detail?: unknown }).detail === "string" ? (body as { detail: string }).detail : "";
  throw new AddressError(
    detail || (Object.keys(fields).length > 0 ? "Revisa los datos marcados." : fallback),
    res.status,
    fields,
  );
}

export async function fetchAddressBook(): Promise<AddressBook> {
  const res = await fetchWithAuth(URL);
  if (!res.ok) return refusal(res, "No se pudieron cargar tus direcciones.");
  return res.json();
}

export async function createAddress(draft: AddressDraft): Promise<SavedAddress> {
  const res = await fetchWithAuth(URL, { method: "POST", body: JSON.stringify(draft) });
  if (!res.ok) return refusal(res, "No se pudo guardar la dirección.");
  return res.json();
}

export async function updateAddress(id: number, draft: AddressDraft): Promise<SavedAddress> {
  const res = await fetchWithAuth(`${URL}${id}/`, { method: "PATCH", body: JSON.stringify(draft) });
  if (!res.ok) return refusal(res, "No se pudo guardar la dirección.");
  return res.json();
}

export async function removeAddress(id: number): Promise<void> {
  const res = await fetchWithAuth(`${URL}${id}/`, { method: "DELETE" });
  if (!res.ok) return refusal(res, "No se pudo quitar la dirección.");
}

export async function makeDefaultAddress(id: number): Promise<SavedAddress> {
  const res = await fetchWithAuth(`${URL}${id}/default/`, { method: "POST", body: "{}" });
  if (!res.ok) return refusal(res, "No se pudo cambiar la dirección predeterminada.");
  return res.json();
}

/** One line a person recognises an entry by. */
export function addressSummary(entry: SavedAddress): string {
  if (entry.kind === "agency") {
    return [entry.carrier_name, entry.agency_name, entry.agency_locality].filter(Boolean).join(" · ");
  }
  return [entry.address_line, entry.district, entry.city].filter(Boolean).join(", ");
}
