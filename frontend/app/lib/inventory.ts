// Phase 6.0 — admin inventory (Kardex), stock reports and INTERNAL sales notes.
// All requests use fetchWithAuth: session cookies + CSRF header. No Bearer, no localStorage.

import { fetchWithAuth } from "./auth";
import { API_BASE } from "./api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type MovementType =
  | "initial_stock"
  | "purchase_entry"
  | "manual_entry"
  | "return_entry"
  | "correction_positive"
  | "manual_exit"
  | "sale_exit"
  | "correction_negative"
  | "damaged_exit"
  | "service_exit";

export type StockMovement = {
  id: number;
  product: number;
  product_name: string;
  product_slug: string;
  movement_type: MovementType;
  movement_type_label: string;
  is_entry: boolean;
  quantity: number;
  signed_quantity: number;
  stock_before: number;
  stock_after: number;
  reason: string;
  reference_type: string;
  reference_id: string;
  order: number | null;
  actor: number | null;
  actor_username: string | null;
  created_at: string;
  metadata: Record<string, unknown>;
};

export type InventoryProduct = {
  id: number;
  name: string;
  slug: string;
  price: string;
  inventory: number;
  is_active: boolean;
  category_name: string | null;
};

export type BestSellingRow = {
  product_id: number;
  product_name: string;
  units_sold: number;
  revenue: string;
};

export type InventorySummary = {
  total_products: number;
  active_products: number;
  out_of_stock_count: number;
  low_stock_count: number;
  total_units: number;
  inventory_value: string;
  low_stock_threshold: number;
  best_selling_product: BestSellingRow | null;
};

export type Paginated<T> = {
  results: T[];
  count: number;
  page: number;
  page_size: number;
};

export type SalesNote = {
  id: number;
  order: number;
  number: string;
  status: string;
  status_label: string;
  issued_at: string | null;
  created_at: string;
  created_by: number | null;
  created_by_username: string | null;
  pdf_generated_at: string | null;
  order_total: string;
  customer_name: string;
  notice?: string;
};

// Manual movement types an operator may register. `sale_exit` is excluded —
// sale movements are only ever produced by the payment pipeline.
export const MANUAL_MOVEMENT_TYPES: { value: MovementType; label: string; isEntry: boolean }[] = [
  { value: "purchase_entry", label: "Entrada por compra", isEntry: true },
  { value: "manual_entry", label: "Entrada manual", isEntry: true },
  { value: "return_entry", label: "Entrada por devolución", isEntry: true },
  { value: "correction_positive", label: "Corrección positiva", isEntry: true },
  { value: "manual_exit", label: "Salida manual", isEntry: false },
  { value: "damaged_exit", label: "Salida por daño / merma", isEntry: false },
  { value: "correction_negative", label: "Corrección negativa", isEntry: false },
];

export const MOVEMENT_TYPE_LABELS: Record<MovementType, string> = {
  initial_stock: "Stock inicial",
  purchase_entry: "Entrada por compra",
  manual_entry: "Entrada manual",
  return_entry: "Entrada por devolución",
  correction_positive: "Corrección positiva",
  manual_exit: "Salida manual",
  sale_exit: "Salida por venta",
  correction_negative: "Corrección negativa",
  damaged_exit: "Salida por daño / merma",
  service_exit: "Salida por servicio técnico",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function buildQuery(params?: Record<string, string | number | undefined | null>): string {
  if (!params) return "";
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    qs.set(key, String(value));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

async function readError(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => null);
  if (body?.detail) return String(body.detail);
  if (body && typeof body === "object") {
    const flat = Object.values(body).flat().filter(Boolean);
    if (flat.length) return flat.join(" ");
  }
  return fallback;
}

async function getJson<T>(path: string, forbiddenMsg: string, fallbackMsg: string): Promise<T> {
  const res = await fetchWithAuth(`${API_BASE}${path}`);
  if (res.status === 403) throw new Error(forbiddenMsg);
  if (!res.ok) throw new Error(await readError(res, fallbackMsg));
  return res.json();
}

// ---------------------------------------------------------------------------
// Inventory reads
// ---------------------------------------------------------------------------

export function fetchInventorySummary(threshold?: number): Promise<InventorySummary> {
  return getJson(
    `/admin/inventory/summary/${buildQuery({ threshold })}`,
    "No tienes permisos para ver el inventario.",
    "No se pudo cargar el resumen de inventario.",
  );
}

export function fetchStockMovements(params?: {
  product?: number;
  movement_type?: string;
  date_from?: string;
  date_to?: string;
  order?: number;
  actor?: number;
  search?: string;
  page?: number;
  page_size?: number;
}): Promise<Paginated<StockMovement>> {
  return getJson(
    `/admin/inventory/movements/${buildQuery(params)}`,
    "No tienes permisos para ver los movimientos de inventario.",
    "No se pudieron cargar los movimientos.",
  );
}

export function fetchLowStock(params?: { threshold?: number; limit?: number }): Promise<{
  threshold: number;
  count: number;
  results: InventoryProduct[];
}> {
  return getJson(
    `/admin/inventory/low-stock/${buildQuery(params)}`,
    "No tienes permisos para ver reportes de stock.",
    "No se pudo cargar el reporte de bajo stock.",
  );
}

export function fetchHighStock(params?: { limit?: number }): Promise<{
  count: number;
  results: InventoryProduct[];
}> {
  return getJson(
    `/admin/inventory/high-stock/${buildQuery(params)}`,
    "No tienes permisos para ver reportes de stock.",
    "No se pudo cargar el reporte de alto stock.",
  );
}

export function fetchBestSelling(params?: {
  date_from?: string;
  date_to?: string;
  limit?: number;
}): Promise<{ count: number; results: BestSellingRow[] }> {
  return getJson(
    `/admin/inventory/best-selling/${buildQuery(params)}`,
    "No tienes permisos para ver reportes de ventas.",
    "No se pudo cargar el reporte de más vendidos.",
  );
}

export function fetchStaleStock(params?: { days?: number; limit?: number }): Promise<{
  days: number;
  count: number;
  results: InventoryProduct[];
}> {
  return getJson(
    `/admin/inventory/no-movement/${buildQuery(params)}`,
    "No tienes permisos para ver reportes de stock.",
    "No se pudo cargar el reporte de productos sin movimiento.",
  );
}

export function fetchStockCard(
  productId: number,
  params?: { limit?: number },
): Promise<{
  product: InventoryProduct;
  current_stock: number;
  movements: StockMovement[];
}> {
  return getJson(
    `/admin/products/${productId}/stock-card/${buildQuery(params)}`,
    "No tienes permisos para ver el Kardex.",
    "No se pudo cargar el Kardex del producto.",
  );
}

// ---------------------------------------------------------------------------
// Inventory writes
// ---------------------------------------------------------------------------

export async function createStockMovement(data: {
  product_id: number;
  movement_type: MovementType;
  quantity: number;
  reason: string;
}): Promise<StockMovement> {
  const res = await fetchWithAuth(`${API_BASE}/admin/inventory/movements/`, {
    method: "POST",
    body: JSON.stringify(data),
  });
  if (res.status === 403) {
    throw new Error("No tienes permisos para registrar movimientos de inventario.");
  }
  if (!res.ok) {
    // 400 covers insufficient stock, quantity<=0 and empty reason — the backend
    // message is the authoritative one, so surface it verbatim.
    throw new Error(await readError(res, "No se pudo registrar el movimiento."));
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Internal sales notes — NOT SUNAT electronic receipts
// ---------------------------------------------------------------------------

export const SALES_NOTE_NOTICE =
  "Documento interno. No reemplaza comprobante SUNAT.";

/** Returns null when the order has no note yet (backend answers 404). */
export async function fetchSalesNote(orderId: number): Promise<SalesNote | null> {
  const res = await fetchWithAuth(`${API_BASE}/admin/orders/${orderId}/sales-note/`);
  if (res.status === 404) return null;
  if (res.status === 403) throw new Error("No tienes permisos para ver notas de venta.");
  if (!res.ok) throw new Error(await readError(res, "No se pudo cargar la nota de venta."));
  return res.json();
}

export async function createSalesNote(orderId: number): Promise<SalesNote> {
  const res = await fetchWithAuth(`${API_BASE}/admin/orders/${orderId}/sales-note/`, {
    method: "POST",
  });
  if (res.status === 403) throw new Error("No tienes permisos para emitir notas de venta.");
  if (!res.ok) throw new Error(await readError(res, "No se pudo emitir la nota de venta."));
  return res.json();
}

/** Downloads the internal note PDF as a blob and triggers a browser save. */
export async function downloadSalesNotePdf(orderId: number, number: string): Promise<void> {
  const res = await fetchWithAuth(`${API_BASE}/admin/orders/${orderId}/sales-note/pdf/`);
  if (res.status === 403) throw new Error("No tienes permisos para descargar notas de venta.");
  if (!res.ok) throw new Error(await readError(res, "No se pudo descargar el PDF."));

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `blackdog-nota-venta-${number}.pdf`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
