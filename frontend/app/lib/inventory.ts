// Phase 6.0 — admin inventory (Kardex), stock reports and INTERNAL sales notes.
// Phase 2D — every read and write is scoped to a BRANCH.
//
// The `branch` parameter throughout is a SELECTION, never authority. The
// backend re-resolves it against the caller's own grants on every request; a
// branch id this client invents answers 404, not somebody else's stock. Passing
// "all" asks for the aggregate of the branches the caller can reach — which for
// a restricted user is not the whole company, and the `scope` in every response
// says which branches it actually covered.
//
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
  | "service_exit"
  | "transfer_in"
  | "transfer_out";

/** Which branches an answer covered. `is_aggregate` means "everything I can see". */
export type InventoryScope = {
  branch: { id: number; name: string } | null;
  branches: { id: number; name: string }[];
  is_aggregate: boolean;
};

/** A branch selection for a request: an id, or "all" for the aggregate. */
export type BranchParam = number | "all" | undefined;

export type StockMovement = {
  id: number;
  company: number;
  branch: number;
  branch_name: string;
  transfer: number | null;
  inventory_count: number | null;
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
  stocked_count: number;
  total_units: number;
  /** Stock x SALE price. Not cost, not capital invested — the platform has no cost model. */
  inventory_value: string;
  inventory_value_basis: "sale_price";
  low_stock_threshold: number;
  best_selling_product: BestSellingRow | null;
  scope: InventoryScope;
};

/** One product's stock in one branch, with its replenishment policy. */
export type BranchStockRow = {
  id: number;
  branch: number;
  branch_name: string;
  product: number;
  product_name: string;
  product_slug: string;
  product_price: string;
  product_is_active: boolean;
  category_name: string | null;
  quantity: number;
  minimum_stock: number;
  target_stock: number;
  needs_replenishment: boolean;
  suggested_quantity: number;
  updated_at: string;
};

export type ReplenishmentRow = {
  branch_id: number;
  branch_name: string;
  product_id: number;
  product_name: string;
  current: number;
  minimum: number;
  target: number;
  suggested_quantity: number;
  surplus_branches?: {
    branch_id: number;
    branch_name: string;
    quantity: number;
    minimum: number;
    surplus: number;
  }[];
};

export type TransferStatus = "draft" | "in_transit" | "received" | "cancelled";

export type StockTransferItem = {
  id: number;
  product: number;
  product_name: string;
  product_slug: string;
  quantity: number;
};

export type StockTransfer = {
  id: number;
  company: number;
  source_branch: number;
  source_branch_name: string;
  destination_branch: number;
  destination_branch_name: string;
  status: TransferStatus;
  status_label: string;
  reason: string;
  reference: string;
  items: StockTransferItem[];
  total_units: number;
  created_by: number | null;
  created_by_username: string | null;
  created_at: string;
  dispatched_at: string | null;
  received_at: string | null;
  cancelled_at: string | null;
  updated_at: string;
};

export type CountStatus = "draft" | "counting" | "review" | "approved" | "cancelled";

export type InventoryCountItem = {
  id: number;
  product: number;
  product_name: string;
  product_slug: string;
  theoretical_at_start: number;
  physical_quantity: number | null;
  theoretical_at_approval: number | null;
  difference: number | null;
  is_counted: boolean;
  note: string;
  updated_at: string;
};

export type InventoryCount = {
  id: number;
  company: number;
  branch: number;
  branch_name: string;
  status: CountStatus;
  status_label: string;
  reason: string;
  items: InventoryCountItem[];
  counted_items: number;
  created_by: number | null;
  created_by_username: string | null;
  created_at: string;
  approved_at: string | null;
  cancelled_at: string | null;
  updated_at: string;
};

export type InventoryBranch = {
  id: number;
  company: number;
  company_name: string;
  name: string;
  address: string;
  phone: string;
  email: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type BranchAccessInfo = {
  company: { id: number; name: string };
  results: InventoryBranch[];
  count: number;
  default_branch: { id: number; name: string } | null;
  access_mode: "all" | "selected" | null;
  allows_aggregate: boolean;
};

export type InventoryDashboard = {
  scope: InventoryScope;
  summary: InventorySummary;
  transfers_in_transit: number;
  pending_counts: number;
  charts: {
    stock_by_branch: { label: string; value: number }[];
    low_stock_by_branch: { label: string; value: number }[];
    entries_trend: { label: string; value: number }[];
    exits_trend: { label: string; value: number }[];
    movement_types: { label: string; value: number }[];
  };
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
  transfer_in: "Entrada por transferencia",
  transfer_out: "Salida por transferencia",
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

function buildQuery(
  params?: Record<string, string | number | undefined | null>,
): string {
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

export function fetchInventorySummary(
  threshold?: number,
  branch?: BranchParam,
): Promise<InventorySummary> {
  return getJson(
    `/admin/inventory/summary/${buildQuery({ threshold, branch })}`,
    "No tienes permisos para ver el inventario.",
    "No se pudo cargar el resumen de inventario.",
  );
}

/** Branches this operator may work in, plus their default. UX data, not authority. */
export function fetchInventoryBranches(): Promise<BranchAccessInfo> {
  return getJson(
    "/admin/inventory/branches/",
    "No tienes permisos para ver el inventario.",
    "No se pudieron cargar las sucursales.",
  );
}

export function fetchInventoryDashboard(params?: {
  branch?: BranchParam;
  threshold?: number;
}): Promise<InventoryDashboard> {
  return getJson(
    `/admin/inventory/dashboard/${buildQuery(params)}`,
    "No tienes permisos para ver el inventario.",
    "No se pudo cargar el panel de inventario.",
  );
}

export function fetchBranchStock(params?: {
  branch?: BranchParam;
  search?: string;
  product?: number;
  status?: "in_stock" | "low_stock" | "out_of_stock";
  threshold?: number;
  page?: number;
  page_size?: number;
}): Promise<Paginated<BranchStockRow> & { scope: InventoryScope }> {
  return getJson(
    `/admin/inventory/stock/${buildQuery(params)}`,
    "No tienes permisos para ver el inventario.",
    "No se pudo cargar el stock.",
  );
}

export function fetchStockMovements(params?: {
  branch?: BranchParam;
  product?: number;
  movement_type?: string;
  date_from?: string;
  date_to?: string;
  order?: number;
  actor?: number;
  search?: string;
  page?: number;
  page_size?: number;
}): Promise<Paginated<StockMovement> & { scope: InventoryScope }> {
  return getJson(
    `/admin/inventory/movements/${buildQuery(params)}`,
    "No tienes permisos para ver los movimientos de inventario.",
    "No se pudieron cargar los movimientos.",
  );
}

export function fetchLowStock(params?: {
  threshold?: number;
  limit?: number;
  branch?: BranchParam;
}): Promise<{
  scope: InventoryScope;
  threshold: number;
  count: number;
  results: BranchStockRow[];
}> {
  return getJson(
    `/admin/inventory/low-stock/${buildQuery(params)}`,
    "No tienes permisos para ver reportes de stock.",
    "No se pudo cargar el reporte de bajo stock.",
  );
}

export function fetchHighStock(params?: { limit?: number; branch?: BranchParam }): Promise<{
  scope: InventoryScope;
  count: number;
  results: BranchStockRow[];
}> {
  return getJson(
    `/admin/inventory/high-stock/${buildQuery(params)}`,
    "No tienes permisos para ver reportes de stock.",
    "No se pudo cargar el reporte de alto stock.",
  );
}

export function fetchReplenishment(params?: {
  branch?: BranchParam;
  limit?: number;
  with_surplus?: "true";
}): Promise<{ scope: InventoryScope; count: number; results: ReplenishmentRow[] }> {
  return getJson(
    `/admin/inventory/replenishment/${buildQuery(params)}`,
    "No tienes permisos para ver reportes de stock.",
    "No se pudo cargar la reposición sugerida.",
  );
}

export function fetchBestSelling(params?: {
  date_from?: string;
  date_to?: string;
  limit?: number;
  branch?: BranchParam;
}): Promise<{ scope: InventoryScope; count: number; results: BestSellingRow[] }> {
  return getJson(
    `/admin/inventory/best-selling/${buildQuery(params)}`,
    "No tienes permisos para ver reportes de ventas.",
    "No se pudo cargar el reporte de más vendidos.",
  );
}

export function fetchStaleStock(params?: {
  days?: number;
  limit?: number;
  branch?: BranchParam;
}): Promise<{
  scope: InventoryScope;
  days: number;
  count: number;
  results: BranchStockRow[];
}> {
  return getJson(
    `/admin/inventory/no-movement/${buildQuery(params)}`,
    "No tienes permisos para ver reportes de stock.",
    "No se pudo cargar el reporte de productos sin movimiento.",
  );
}

export function fetchStockCard(
  productId: number,
  params?: { limit?: number; branch?: BranchParam },
): Promise<{
  scope: InventoryScope;
  product: {
    id: number;
    name: string;
    slug: string;
    price: string;
    is_active: boolean;
    category_name: string | null;
  };
  current_stock: number;
  stock_by_branch: BranchStockRow[];
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

async function writeJson<T>(
  path: string,
  init: RequestInit,
  forbiddenMsg: string,
  fallbackMsg: string,
): Promise<T> {
  const res = await fetchWithAuth(`${API_BASE}${path}`, init);
  if (res.status === 403) throw new Error(await readError(res, forbiddenMsg));
  if (res.status === 404) throw new Error(await readError(res, "No encontrado."));
  // 400 covers insufficient stock, an invalid quantity, an empty reason and every
  // lifecycle refusal — the backend message is the authoritative one, so it is
  // surfaced verbatim rather than replaced with a guess.
  if (!res.ok) throw new Error(await readError(res, fallbackMsg));
  return res.json();
}

export function createStockMovement(data: {
  product_id: number;
  branch?: number;
  movement_type: MovementType;
  quantity: number;
  reason: string;
}): Promise<StockMovement> {
  return writeJson(
    "/admin/inventory/movements/",
    { method: "POST", body: JSON.stringify(data) },
    "No tienes permisos para registrar movimientos de inventario.",
    "No se pudo registrar el movimiento.",
  );
}

/** Replenishment policy only. Quantity is never editable — that needs a movement. */
export function updateStockPolicy(
  stockId: number,
  data: { minimum_stock?: number; target_stock?: number },
): Promise<BranchStockRow> {
  return writeJson(
    `/admin/inventory/stock/${stockId}/policy/`,
    { method: "PATCH", body: JSON.stringify(data) },
    "No tienes permisos para configurar el inventario.",
    "No se pudo guardar la política de reposición.",
  );
}

// --- Transfers -------------------------------------------------------------

export function fetchTransfers(params?: {
  status?: TransferStatus;
  branch?: BranchParam;
  page?: number;
  page_size?: number;
}): Promise<Paginated<StockTransfer>> {
  return getJson(
    `/admin/inventory/transfers/${buildQuery(params)}`,
    "No tienes permisos para ver transferencias.",
    "No se pudieron cargar las transferencias.",
  );
}

export function fetchTransfer(id: number): Promise<StockTransfer> {
  return getJson(
    `/admin/inventory/transfers/${id}/`,
    "No tienes permisos para ver transferencias.",
    "No se pudo cargar la transferencia.",
  );
}

export function createTransfer(data: {
  source_branch: number;
  destination_branch: number;
  reason?: string;
  reference?: string;
}): Promise<StockTransfer> {
  return writeJson(
    "/admin/inventory/transfers/",
    { method: "POST", body: JSON.stringify(data) },
    "No tienes permisos para crear transferencias.",
    "No se pudo crear la transferencia.",
  );
}

/** Whole-list replace: lines not sent are removed. Quantity 0 removes a line. */
export function setTransferItems(
  id: number,
  items: { product: number; quantity: number }[],
): Promise<StockTransfer> {
  return writeJson(
    `/admin/inventory/transfers/${id}/items/`,
    { method: "PUT", body: JSON.stringify(items) },
    "No tienes permisos para editar transferencias.",
    "No se pudieron guardar las líneas.",
  );
}

function transferAction(id: number, action: string, fallback: string) {
  return writeJson<StockTransfer>(
    `/admin/inventory/transfers/${id}/${action}/`,
    { method: "POST" },
    "No tienes permisos para operar transferencias.",
    fallback,
  );
}

export const dispatchTransfer = (id: number) =>
  transferAction(id, "dispatch", "No se pudo despachar la transferencia.");
export const receiveTransfer = (id: number) =>
  transferAction(id, "receive", "No se pudo recibir la transferencia.");
export const cancelTransfer = (id: number) =>
  transferAction(id, "cancel", "No se pudo anular la transferencia.");

// --- Physical counts -------------------------------------------------------

export function fetchCounts(params?: {
  status?: CountStatus;
  branch?: BranchParam;
  page?: number;
  page_size?: number;
}): Promise<Paginated<InventoryCount> & { scope: InventoryScope }> {
  return getJson(
    `/admin/inventory/counts/${buildQuery(params)}`,
    "No tienes permisos para ver recuentos.",
    "No se pudieron cargar los recuentos.",
  );
}

export function fetchCount(id: number): Promise<InventoryCount> {
  return getJson(
    `/admin/inventory/counts/${id}/`,
    "No tienes permisos para ver recuentos.",
    "No se pudo cargar el recuento.",
  );
}

export function createCount(data: { branch?: number; reason?: string }): Promise<InventoryCount> {
  return writeJson(
    "/admin/inventory/counts/",
    { method: "POST", body: JSON.stringify(data) },
    "No tienes permisos para crear recuentos.",
    "No se pudo crear el recuento.",
  );
}

/**
 * Additive: sending a subset updates those products and leaves the rest alone.
 * `physical_quantity: null` means NOT COUNTED, which is not the same as zero.
 */
export function setCountItems(
  id: number,
  items: { product: number; physical_quantity: number | null; note?: string }[],
): Promise<InventoryCount> {
  return writeJson(
    `/admin/inventory/counts/${id}/items/`,
    { method: "PUT", body: JSON.stringify(items) },
    "No tienes permisos para editar recuentos.",
    "No se pudieron guardar las cantidades.",
  );
}

export function approveCount(
  id: number,
): Promise<InventoryCount & { movements: StockMovement[] }> {
  return writeJson(
    `/admin/inventory/counts/${id}/approve/`,
    { method: "POST" },
    "No tienes permisos para aprobar recuentos.",
    "No se pudo aprobar el recuento.",
  );
}

export function cancelCount(id: number): Promise<InventoryCount> {
  return writeJson(
    `/admin/inventory/counts/${id}/cancel/`,
    { method: "POST" },
    "No tienes permisos para anular recuentos.",
    "No se pudo anular el recuento.",
  );
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
