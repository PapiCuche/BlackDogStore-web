/**
 * SUNAT-SERIES-PANEL (C3) — the fiscal series of a company, as a platform master manages them.
 *
 * The server decides everything: who may (only the master, 403 otherwise), SUNAT's name rules,
 * one active series per slot, and that `next_number` and deletion are only possible while
 * nothing was issued with the series. This file only carries the requests.
 */

import { API_BASE } from "./api";
import { fetchWithAuth } from "./auth";

export type FiscalSeriesRow = {
  id: number;
  company: number;
  branch: { id: number; name: string } | null;
  document_type: string;
  document_label: string;
  series: string;
  environment: "beta" | "production";
  next_number: number;
  is_active: boolean;
  used: boolean;
  next_number_editable: boolean;
  deletable: boolean;
};

export type FiscalSeriesCompany = { id: number; name: string; slug: string; tax_id: string };

export type FiscalSeriesListing = {
  companies: FiscalSeriesCompany[];
  document_types: { value: string; label: string; letters: string[] }[];
  environments: { value: string; label: string }[];
  company: FiscalSeriesCompany | null;
  branches: { id: number; name: string }[];
  series: FiscalSeriesRow[];
};

export type NewFiscalSeries = {
  company: number;
  branch: number | null;
  document_type: string;
  series: string;
  environment: string;
  next_number: number;
  is_active: boolean;
};

export class FiscalSeriesError extends Error {
  readonly status: number;
  readonly fields: Record<string, string>;

  constructor(message: string, status: number, fields: Record<string, string> = {}) {
    super(message);
    this.name = "FiscalSeriesError";
    this.status = status;
    this.fields = fields;
  }
}

const BASE = `${API_BASE}/admin/fiscal-series/`;

async function read<T>(res: Response, fallback: string): Promise<T> {
  if (res.status === 204) return undefined as T;
  const body = (await res.json().catch(() => null)) as Record<string, unknown> | null;
  if (res.ok && body) return body as T;
  const fields: Record<string, string> = {};
  if (body && typeof body.errors === "object" && body.errors) {
    for (const [name, message] of Object.entries(body.errors as Record<string, unknown>)) fields[name] = String(message);
  }
  throw new FiscalSeriesError(body?.detail ? String(body.detail) : fallback, res.status, fields);
}

export async function fetchFiscalSeries(companyId: number | null): Promise<FiscalSeriesListing> {
  const url = companyId ? `${BASE}?company=${encodeURIComponent(String(companyId))}` : BASE;
  return read(await fetchWithAuth(url), "No se pudieron consultar las series.");
}

export async function createFiscalSeries(body: NewFiscalSeries): Promise<FiscalSeriesRow> {
  return read(await fetchWithAuth(BASE, { method: "POST", body: JSON.stringify(body) }), "No se pudo crear la serie.");
}

export async function updateFiscalSeries(
  id: number, body: { is_active?: boolean; next_number?: number },
): Promise<FiscalSeriesRow> {
  return read(
    await fetchWithAuth(`${BASE}${id}/`, { method: "PATCH", body: JSON.stringify(body) }),
    "No se pudo cambiar la serie.",
  );
}

export async function deleteFiscalSeries(id: number): Promise<void> {
  return read(await fetchWithAuth(`${BASE}${id}/`, { method: "DELETE" }), "No se pudo borrar la serie.");
}
