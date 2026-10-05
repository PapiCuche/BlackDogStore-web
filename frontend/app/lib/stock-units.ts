/**
 * SERIALIZED-STOCK — devices tracked by serial number, as part of the stock.
 *
 * This layer validates nothing about an identifier and counts nothing: the
 * server decides whether an IMEI is valid, whether a device may enter, and how
 * many are on the shelf. What it is told to send, it sends as typed.
 */

import { API_BASE } from "./api";
import { fetchWithAuth } from "./auth";

export type StockUnit = {
  id: number;
  product_id: number;
  product_name: string;
  branch_id: number;
  branch_name: string;
  serial_number: string;
  imei: string | null;
  imei2: string | null;
  condition: string;
  condition_label: string;
  status: "available" | "reserved" | "sold" | "written_off";
  status_label: string;
  cost: string | null;
  price_override: string | null;
  catalogue_price: string;
  received_at: string;
  sold_at: string | null;
  order_id: number | null;
  notes: string;
};

export type Option = { value: string; label: string };

export type StockUnitPage = {
  results: StockUnit[];
  count: number;
  page: number;
  page_size: number;
  statuses: Option[];
  conditions: Option[];
};

export type SerializedProduct = { id: number; name: string; requires_imei: boolean; price: string };

export type NewUnit = { serial_number: string; imei: string; imei2: string; condition: string; cost: string };

/** A refusal. `line` is the 1-based device of a batch the server turned down. */
export class StockUnitApiError extends Error {
  readonly status: number;
  readonly line: number | null;

  constructor(message: string, status: number, line: number | null = null) {
    super(message);
    this.name = "StockUnitApiError";
    this.status = status;
    this.line = line;
  }
}

const BASE = `${API_BASE}/admin/inventory/units`;

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetchWithAuth(`${BASE}${path}`, init);
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? String(body.detail) : "";
    const line = body && typeof body === "object" && typeof body.line === "number" ? body.line : null;
    throw new StockUnitApiError(
      detail || (res.status === 403 ? "No tienes permisos sobre el inventario." : "No se pudo completar la operación."),
      res.status,
      line,
    );
  }
  return body as T;
}

const post = <T,>(path: string, payload: unknown = {}) =>
  call<T>(path, { method: "POST", body: JSON.stringify(payload) });

export function fetchStockUnits(params: {
  branch: number | "all" | undefined;
  product?: string;
  status?: string;
  condition?: string;
  search?: string;
  page?: number;
}): Promise<StockUnitPage> {
  const qs = new URLSearchParams();
  if (params.branch !== undefined) qs.set("branch", String(params.branch));
  for (const key of ["product", "status", "condition", "search"] as const) {
    const value = params[key];
    if (value) qs.set(key, value);
  }
  if (params.page && params.page > 1) qs.set("page", String(params.page));
  const query = qs.toString();
  return call<StockUnitPage>(`/${query ? `?${query}` : ""}`);
}

export const fetchSerializedProducts = () =>
  call<{ results: SerializedProduct[] }>("/products/");

export const receiveStockUnits = (body: {
  product_id: number; branch: number; reason: string; units: NewUnit[];
}) => post<{ results: StockUnit[] }>("/", body);

export type UnitAction = "reserve" | "release" | "write-off" | "return";

export const actOnStockUnit = (id: number, action: UnitAction, reason = "") =>
  post<StockUnit>(`/${id}/${action}/`, { reason });
