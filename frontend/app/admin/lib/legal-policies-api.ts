/**
 * LEGAL-POLICIES-01 — the admin side of the three legal documents.
 * The company comes from the session; `companyId` only selects among the caller's own.
 */
import { fetchWithAuth } from "../../lib/auth";
import { API_BASE } from "../../lib/api";
import type { LegalKind } from "../../lib/legal-policies";

export type AdminLegalPolicy = {
  kind: LegalKind;
  draft_text: string;
  published_text: string;
  is_published: boolean;
  published_at: string | null;
  has_unpublished_changes: boolean;
  updated_at: string | null;
};

export type AdminLegalPolicies = {
  company: { id: number; name: string };
  max_length: number;
  can_manage: boolean;
  policies: AdminLegalPolicy[];
};

export class LegalPolicyError extends Error {
  constructor(message: string, readonly errors: Record<string, string[]> = {}, readonly status = 0) {
    super(message);
    this.name = "LegalPolicyError";
  }
}

function query(companyId?: number | null): string {
  return companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
}

async function handle<T>(res: Response): Promise<T> {
  if (res.ok) return (await res.json()) as T;
  const data = await res.json().catch(() => ({}) as Record<string, unknown>);
  const detail = typeof data.detail === "string" && data.detail
    ? data.detail
    : res.status === 403 ? "No tienes permisos para cambiar las políticas de esta empresa."
      : res.status === 404 ? "Empresa o documento no encontrado."
        : "No se pudo completar la operación.";
  const errors = (data.errors && typeof data.errors === "object" ? data.errors : {}) as Record<string, string[]>;
  throw new LegalPolicyError(detail, errors, res.status);
}

export async function fetchLegalPolicies(companyId?: number | null): Promise<AdminLegalPolicies> {
  return handle(await fetchWithAuth(`${API_BASE}/admin/legal-policies/${query(companyId)}`));
}

export async function saveLegalPolicyDraft(
  kind: LegalKind, draftText: string, companyId?: number | null,
): Promise<AdminLegalPolicy> {
  return handle(await fetchWithAuth(`${API_BASE}/admin/legal-policies/${kind}/${query(companyId)}`, {
    method: "PUT", body: JSON.stringify({ draft_text: draftText }),
  }));
}

export async function publishLegalPolicy(kind: LegalKind, companyId?: number | null): Promise<AdminLegalPolicy> {
  return handle(await fetchWithAuth(`${API_BASE}/admin/legal-policies/${kind}/publish/${query(companyId)}`, {
    method: "POST",
  }));
}

export async function unpublishLegalPolicy(kind: LegalKind, companyId?: number | null): Promise<AdminLegalPolicy> {
  return handle(await fetchWithAuth(`${API_BASE}/admin/legal-policies/${kind}/unpublish/${query(companyId)}`, {
    method: "POST",
  }));
}
