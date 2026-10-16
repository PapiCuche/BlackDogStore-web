import { API_BASE } from "./api";
import { fetchWithAuth } from "./auth";

export type AdminUser = {
  id: number;
  username: string;
  email: string;
  is_active: boolean;
  role: string;
  date_joined: string;
};

export type PaginatedResponse<T> = {
  count: number;
  page: number;
  page_size: number;
  results: T[];
};

export type AuditLogEntry = {
  id: number;
  actor: string | null;
  action: string;
  target_type: string;
  target_id: string;
  metadata: Record<string, unknown>;
  ip_address: string | null;
  created_at: string;
};

function buildQs(params: Record<string, string | number | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && String(v) !== '') p.set(k, String(v));
  }
  return p.size > 0 ? `?${p.toString()}` : '';
}

export async function fetchAdminUsers(params?: {
  search?: string;
  role?: string;
  page?: number;
  page_size?: number;
}): Promise<PaginatedResponse<AdminUser>> {
  const qs = buildQs({
    search: params?.search,
    role: params?.role,
    page: params?.page,
    page_size: params?.page_size,
  });
  const res = await fetchWithAuth(`${API_BASE}/admin/users/${qs}`);
  if (res.status === 403) throw new Error('No tienes permisos para ver usuarios.');
  if (res.status === 401) throw new Error('Sesión expirada. Vuelve a iniciar sesión.');
  if (!res.ok) throw new Error('No se pudieron cargar los usuarios.');
  return res.json();
}

export async function changeUserRole(userId: number, newRole: string): Promise<void> {
  const res = await fetchWithAuth(`${API_BASE}/admin/users/${userId}/role/`, {
    method: 'PATCH',
    body: JSON.stringify({ role: newRole }),
  });
  if (res.status === 403) throw new Error('Solo el superadministrador puede cambiar roles.');
  if (res.status === 400) {
    const err = await res.json().catch(() => null);
    throw new Error(err?.detail ?? 'Solicitud inválida.');
  }
  if (!res.ok) throw new Error('No se pudo actualizar el rol.');
}

export async function fetchAuditLogs(params?: {
  action?: string;
  actor?: string;
  target_type?: string;
  page?: number;
  page_size?: number;
}): Promise<PaginatedResponse<AuditLogEntry>> {
  const qs = buildQs({
    action: params?.action,
    actor: params?.actor,
    target_type: params?.target_type,
    page: params?.page,
    page_size: params?.page_size,
  });
  const res = await fetchWithAuth(`${API_BASE}/admin/audit-logs/${qs}`);
  if (res.status === 403) throw new Error('No tienes permisos para ver el registro de auditoría.');
  if (res.status === 401) throw new Error('Sesión expirada. Vuelve a iniciar sesión.');
  if (!res.ok) throw new Error('No se pudieron cargar los registros.');
  return res.json();
}

// ---------------------------------------------------------------------------
// Admin products + categories types
// ---------------------------------------------------------------------------

export type AdminProduct = {
  id: number;
  name: string;
  slug: string;
  description: string;
  price: string;
  inventory: number;
  image_url: string;
  category_id: number | null;
  category_name: string | null;
  is_active: boolean;
  /**
   * PRODUCT-DESTINATION-01. `false` = «sólo stock interno»: en uso dentro de la
   * empresa (caja, inventario, taller) y fuera de la tienda en línea. Otro dato
   * que `is_active`.
   */
  is_published_online: boolean;
  /** Cómo se cuenta: equipo por equipo (con serie) o por cantidad. */
  is_serialized?: boolean;
  requires_imei?: boolean;
  /**
   * INVENTORY-UNIFIED-01. Códigos de lo que le falta para publicarse en bloque
   * (`PUBLICATION_PROBLEM_LABELS`). Vacío = listo. La ficha individual no lo exige.
   */
  publication_problems?: string[];
  /** A1: the catalogue's reference cost. Only in the detail, and only for `costs.view`. */
  reference_cost?: string | null;
  created_at: string;
  updated_at: string;
  /** La galería. Sólo viene en el detalle; `image_url` es la principal. */
  images?: AdminProductImage[];
  /** DC3: `stock` = sólo stock propio; `stock_or_order` = también bajo pedido. */
  sale_mode?: 'stock' | 'stock_or_order';
  /** COMMERCE-ADMIN-UX-01: línea del equipo (DC2) y su modelo, si es una variante. */
  condition_line?: 'new' | 'used';
  product_model_id?: number | null;
};

/** Una imagen de la galería de un producto. */
export type AdminProductImage = {
  id: number;
  url: string;
  alt_text: string;
  is_primary: boolean;
  sort_order: number;
  width: number;
  height: number;
};

/** Lo que el servidor acepta. Aquí sólo evita un viaje que acabaría en rechazo. */
export const PRODUCT_IMAGE_ACCEPT = "image/png,image/jpeg,image/webp";
export const PRODUCT_IMAGE_MAX_BYTES = 8 * 1024 * 1024;

async function galleryError(res: Response, fallback: string): Promise<Error> {
  const body = await res.json().catch(() => null);
  if (body?.detail) return new Error(String(body.detail));
  if (res.status === 413) return new Error("La imagen pesa más de 8 MB.");
  if (res.status === 403) return new Error("No tienes permiso para cambiar las imágenes de este producto.");
  return new Error(fallback);
}

export async function fetchProductImages(productId: number): Promise<AdminProductImage[]> {
  const res = await fetchWithAuth(`${API_BASE}/admin/products/${productId}/images/`);
  if (!res.ok) throw await galleryError(res, "No se pudo cargar la galería.");
  return (await res.json()).results;
}

export async function uploadProductImage(
  productId: number, file: File, altText = "",
): Promise<AdminProductImage> {
  const form = new FormData();
  form.append("file", file);
  if (altText) form.append("alt_text", altText);
  // Sin `Content-Type`: con un `FormData` lo escribe el navegador, con el boundary.
  const res = await fetchWithAuth(`${API_BASE}/admin/products/${productId}/images/`, {
    method: "POST", body: form,
  });
  if (!res.ok) throw await galleryError(res, "No se pudo subir la imagen.");
  return res.json();
}

export async function patchProductImage(
  productId: number, imageId: number, data: { alt_text?: string; is_primary?: true },
): Promise<AdminProductImage> {
  const res = await fetchWithAuth(`${API_BASE}/admin/products/${productId}/images/${imageId}/`, {
    method: "PATCH", body: JSON.stringify(data),
  });
  if (!res.ok) throw await galleryError(res, "No se pudo guardar el cambio.");
  return res.json();
}

/** Quita la imagen y devuelve la galería como quedó (el servidor elige la nueva principal). */
export async function deleteProductImage(
  productId: number, imageId: number,
): Promise<AdminProductImage[]> {
  const res = await fetchWithAuth(`${API_BASE}/admin/products/${productId}/images/${imageId}/`, {
    method: "DELETE",
  });
  if (!res.ok) throw await galleryError(res, "No se pudo quitar la imagen.");
  return fetchProductImages(productId);
}

export async function reorderProductImages(
  productId: number, order: number[],
): Promise<AdminProductImage[]> {
  const res = await fetchWithAuth(`${API_BASE}/admin/products/${productId}/images/order/`, {
    method: "POST", body: JSON.stringify({ order }),
  });
  if (!res.ok) throw await galleryError(res, "No se pudo guardar el orden.");
  return (await res.json()).results;
}

export type AdminCategory = {
  id: number;
  name: string;
  slug: string;
  /** Con la que la portada presenta la categoría. Vacía = sin imagen. */
  image_url?: string;
  /** Si se ofrece al público. Retirarla no oculta sus productos. */
  is_active?: boolean;
  /** Si la portada la ilustra entre sus familias. */
  show_on_home?: boolean;
  /** Orden ascendente en la tienda; los empates van por nombre. */
  home_order?: number;
  product_count?: number;
};

/**
 * Los filtros de la lista de productos. La acción en bloque manda ESTOS mismos
 * para «todo lo que coincide»: el servidor los vuelve a evaluar con el código
 * que contó la lista.
 */
export type ProductListFilter = {
  search?: string;
  category?: number | string;
  is_active?: 'true' | 'false';
  is_published_online?: 'true' | 'false';
  is_serialized?: 'true' | 'false';
  stock?: 'in_stock' | 'out_of_stock' | 'low_stock';
  /** COMMERCE-ADMIN-UX-01: línea nueva o seminueva (DC2). */
  condition_line?: 'new' | 'used';
  /** COMMERCE-ADMIN-UX-01: «Bajo pedido» (DC3). */
  sale_mode?: 'stock' | 'stock_or_order';
};

export async function fetchAdminProducts(params?: ProductListFilter & {
  page?: number;
  page_size?: number;
  /**
   * La empresa para la que se abrió la pantalla. Explícita: sin ella la añade
   * `fetchWithAuth` al enviar, leyendo lo que el navegador tenga guardado EN ESE
   * MOMENTO — que otra pestaña puede haber cambiado.
   */
  company?: number | null;
}): Promise<PaginatedResponse<AdminProduct>> {
  const qs = buildQs({
    company: params?.company ?? undefined,
    search: params?.search,
    category: params?.category !== undefined ? String(params.category) : undefined,
    is_active: params?.is_active,
    is_published_online: params?.is_published_online,
    is_serialized: params?.is_serialized,
    stock: params?.stock,
    condition_line: params?.condition_line,
    sale_mode: params?.sale_mode,
    page: params?.page,
    page_size: params?.page_size,
  });
  const res = await fetchWithAuth(`${API_BASE}/admin/products/${qs}`);
  if (res.status === 403) throw new Error('No tienes permisos para ver productos.');
  if (res.status === 401) throw new Error('Sesión expirada. Vuelve a iniciar sesión.');
  if (!res.ok) throw new Error('No se pudieron cargar los productos.');
  return res.json();
}

/** Lo que le falta a un producto para publicarse en bloque, como lo dice el panel. */
export const PUBLICATION_PROBLEM_LABELS: Record<string, string> = {
  inactive: 'está inactivo',
  no_price: 'no tiene precio de venta',
  no_image: 'no tiene imagen',
  no_category: 'no tiene categoría',
};

/** Un código que esta versión no conoce se muestra tal cual: mejor que callarlo. */
export function publicationProblemLabel(code: string): string {
  return PUBLICATION_PROBLEM_LABELS[code] ?? code;
}

export type BulkVisibilityAction = 'publish' | 'hide';

export type BulkVisibilityResult = {
  action: BulkVisibilityAction;
  changed: number;
  /** Ya estaban como se pidió. */
  unchanged: number;
  /** Sólo al publicar: los que no están listos, con el motivo. Los demás SÍ se publicaron. */
  refused: { id: number; name: string; problems: string[] }[];
  total: number;
};

/** «Todo lo que coincide» ya no coincide con lo que se contó. No se cambió nada. */
export class SelectionChangedError extends Error {
  currentCount: number;

  constructor(currentCount: number) {
    super(`La selección cambió: ahora coinciden ${currentCount} productos. No se cambió nada.`);
    this.name = 'SelectionChangedError';
    this.currentCount = currentCount;
  }
}

/**
 * Publica u oculta varios productos. Sólo cambia «en la web»: ni activo, ni
 * precio, ni stock. O los marcados (`ids`) o todo lo que coincide con el filtro,
 * con el total que se mostró (`expectedCount`).
 */
export async function bulkSetProductVisibility(
  action: BulkVisibilityAction,
  selection: { ids: number[] } | { filter: ProductListFilter; expectedCount: number },
  /**
   * La empresa de la lista sobre la que se seleccionó. «Todo lo que coincide»
   * viaja como filtro y cuenta, sin ids: en otra empresa podría coincidir otra
   * cosa. Por eso va escrita en la dirección y no se deja a lo que el navegador
   * tenga guardado al enviar.
   */
  companyId?: number | null,
): Promise<BulkVisibilityResult> {
  const body = 'ids' in selection
    ? { action, ids: selection.ids }
    : {
        action,
        filter: Object.fromEntries(
          Object.entries(selection.filter).filter(([, value]) => value !== undefined && String(value) !== ''),
        ),
        expected_count: selection.expectedCount,
      };
  const res = await fetchWithAuth(`${API_BASE}/admin/products/bulk-visibility/${buildQs({ company: companyId ?? undefined })}`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  if (res.ok) return res.json();
  const err = await res.json().catch(() => null);
  if (res.status === 409 && err?.code === 'selection_changed') {
    throw new SelectionChangedError(Number(err.current_count) || 0);
  }
  if (res.status === 403) throw new Error('No tienes permisos para publicar u ocultar productos.');
  if (res.status === 401) throw new Error('Sesión expirada. Vuelve a iniciar sesión.');
  if (res.status === 404) {
    throw new Error('Alguno de los productos ya no existe. Actualiza la lista y vuelve a seleccionar.');
  }
  throw new Error(typeof err?.detail === 'string' ? err.detail : 'No se pudo cambiar la visibilidad de los productos.');
}

export async function fetchAdminProductDetail(productId: number): Promise<AdminProduct> {
  const res = await fetchWithAuth(`${API_BASE}/admin/products/${productId}/`);
  if (res.status === 403) throw new Error('No tienes permisos.');
  if (res.status === 404) throw new Error('Producto no encontrado.');
  if (!res.ok) throw new Error('No se pudo cargar el producto.');
  return res.json();
}

export async function createAdminProduct(data: {
  name: string;
  price: string;
  inventory: number;
  description?: string;
  slug?: string;
  image_url?: string;
  category?: number | null;
  is_active?: boolean;
  is_published_online?: boolean;
}): Promise<AdminProduct> {
  const res = await fetchWithAuth(`${API_BASE}/admin/products/`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
  if (res.status === 400) {
    const err = await res.json().catch(() => null);
    const msg = err && typeof err === 'object'
      ? Object.values(err).flat().join(' ')
      : 'Datos inválidos.';
    throw new Error(msg);
  }
  if (res.status === 403) throw new Error('Solo admin puede crear productos.');
  if (!res.ok) throw new Error('No se pudo crear el producto.');
  return res.json();
}

/**
 * Edit a product. `inventory` is deliberately ABSENT from the shape.
 *
 * Phase 2D: stock is a derived aggregate over BranchStock, and the backend
 * rejects `inventory` on PATCH. Changing stock is a movement — it needs a
 * branch, an actor and a reason — so it goes through /admin/inventory/movements/
 * or an approved physical count.
 */
export async function patchAdminProduct(
  productId: number,
  data: Partial<{
    name: string;
    slug: string;
    description: string;
    price: string;
    image_url: string;
    category: number | null;
    is_active: boolean;
    is_published_online: boolean;
    /** A1: `costs.view` + `products.manage`. Empty or null clears it. */
    reference_cost: string | null;
  }>,
): Promise<AdminProduct> {
  const res = await fetchWithAuth(`${API_BASE}/admin/products/${productId}/`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
  if (res.status === 400) {
    const err = await res.json().catch(() => null);
    const msg = err && typeof err === 'object'
      ? Object.values(err).flat().join(' ')
      : 'Datos inválidos.';
    throw new Error(msg);
  }
  if (res.status === 403) {
    // A1: the server says WHICH permission is missing (the reference cost needs
    // «Ver costos y márgenes»); a generic text would hide it.
    const err = await Promise.resolve().then(() => res.json()).catch(() => null);
    throw new Error(err?.detail ? String(err.detail) : 'No tienes permisos para editar productos.');
  }
  if (res.status === 404) throw new Error('Producto no encontrado.');
  if (!res.ok) throw new Error('No se pudo actualizar el producto.');
  return res.json();
}

export async function adjustInventory(
  productId: number,
  delta: number,
  reason: string,
  /** Omitted: the server applies it to the caller's default branch. */
  branch?: number,
): Promise<AdminProduct> {
  const res = await fetchWithAuth(`${API_BASE}/admin/products/${productId}/inventory-adjust/`, {
    method: 'POST',
    body: JSON.stringify(branch === undefined ? { delta, reason } : { delta, reason, branch }),
  });
  if (res.status === 400) {
    const err = await res.json().catch(() => null);
    const msg = err?.detail ?? (err && typeof err === 'object'
      ? Object.values(err).flat().join(' ')
      : 'Error de validación.');
    throw new Error(msg);
  }
  if (res.status === 403) throw new Error('No tienes permisos para ajustar inventario.');
  if (res.status === 404) throw new Error('Producto no encontrado.');
  if (!res.ok) throw new Error('No se pudo ajustar el inventario.');
  return res.json();
}

/** Las categorías de una empresa. Sin `companyId`, las de la empresa de quien pregunta. */
export async function fetchAdminCategories(companyId?: number | null): Promise<AdminCategory[]> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : '';
  const res = await fetchWithAuth(`${API_BASE}/admin/categories/${qs}`);
  if (res.status === 403) throw new Error('No tienes permisos para ver categorías.');
  if (!res.ok) throw new Error('No se pudieron cargar las categorías.');
  return res.json();
}

export async function createAdminCategory(data: {
  name: string;
  slug?: string;
}): Promise<AdminCategory> {
  const res = await fetchWithAuth(`${API_BASE}/admin/categories/`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
  if (res.status === 400) {
    const err = await res.json().catch(() => null);
    const msg = err && typeof err === 'object'
      ? Object.values(err).flat().join(' ')
      : 'Datos inválidos.';
    throw new Error(msg);
  }
  if (res.status === 403) throw new Error('Solo admin puede crear categorías.');
  if (!res.ok) throw new Error('No se pudo crear la categoría.');
  return res.json();
}

export async function updateAdminCategory(
  categoryId: number,
  data: {
    name?: string; image_url?: string;
    is_active?: boolean; show_on_home?: boolean; home_order?: number;
  },
): Promise<AdminCategory> {
  const res = await fetchWithAuth(`${API_BASE}/admin/categories/${categoryId}/`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
  if (res.status === 400) {
    const err = await res.json().catch(() => null);
    const msg = err && typeof err === 'object'
      ? Object.values(err).flat().join(' ')
      : 'Datos inválidos.';
    throw new Error(msg);
  }
  if (res.status === 403) throw new Error('No tienes permisos para editar categorías.');
  if (res.status === 404) throw new Error('Categoría no encontrada.');
  if (!res.ok) throw new Error('No se pudo guardar la categoría.');
  return res.json();
}

// ---------------------------------------------------------------------------
// Admin orders types and helpers
// ---------------------------------------------------------------------------

/** SERIAL-PICK-01 — un equipo, como se muestra para confirmar que es ese. */
export type UnitDescription = {
  unit_id: number;
  product_id: number;
  product_name: string;
  serial_number: string;
  imei: string;
  imei2: string;
  condition: string;
  condition_label: string;
  requires_imei: boolean;
};

/** Un equipo que alguien ya identificó como el de este pedido. */
export type OrderUnitRecord = {
  id: number;
  serial_number: string;
  imei: string;
  imei2: string;
  method: string;
  method_label: string;
  identified_at: string;
  identified_by: string;
  /** Falso cuando el equipo ya salió con el pedido: no se quita, diga lo que diga el estado. */
  can_remove?: boolean;
};

export type AdminOrderItem = {
  id: number;
  product_id: number;
  product_name: string;
  quantity: number;
  price: string;
  subtotal: string;
  /** Se vende equipo por equipo: hay que identificar cada uno al entregar. */
  is_serialized?: boolean;
  /** A mano, sus equipos se identifican por el IMEI. */
  requires_imei?: boolean;
  units?: OrderUnitRecord[];
  units_pending?: number;
  /** DC3 — del estante (`own`) o bajo pedido (`supplier`). */
  supply_origin?: 'own' | 'supplier';
  /** '' | 'awaiting_payment' | 'pending' | 'to_confirm' | 'failed' | 'received'. */
  supply_status?: string;
  supply_note?: string;
  lead_time_days?: number | null;
  supplier_offer_id?: number | null;
  supplier_name?: string;
  /** Recibir del proveedor pide una serie/IMEI por cada unidad de la línea. */
  product_is_serialized?: boolean;
};

export type AdminOrder = {
  id: number;
  customer_name: string;
  customer_email: string;
  user_id: number | null;
  username: string | null;
  total: string;
  status: string;
  fulfillment_status: string;
  paid: boolean;
  created_at: string;
  paid_at: string | null;
  item_count: number;
  /**
   * INV-04: the order is paid but stock could not cover at least one line, so
   * units it sold never left a shelf. Derived server-side from the Kardex.
   */
  has_stock_shortfall: boolean;
  /**
   * CLIENT-SEC-01: how the order looked when it was made online, and whether a
   * person still has to look. Both null for an order that was not assessed (a
   * sale at the till, an order from before). Said by the server.
   */
  risk_level?: OrderRiskLevel | null;
  review_status?: OrderReviewStatus | null;
  /** DC3: cómo va el abastecimiento de lo vendido bajo pedido ('' si no hay nada bajo pedido). */
  supply_status?: string;
};

export type OrderRiskLevel = 'low' | 'medium' | 'high';
export type OrderReviewStatus = 'not_required' | 'pending' | 'approved' | 'rejected';

/**
 * CLIENT-SEC-01: what was noticed about an order and what a person decided.
 * The reasons are the server's words; nothing of the buyer travels here.
 */
export type OrderRisk = {
  level: OrderRiskLevel;
  level_label: string;
  score: number;
  signals: { code: string; label: string }[];
  review_status: OrderReviewStatus;
  review_status_label: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_note: string;
  assessed_at: string;
  /** Whether THIS caller may decide the review, now. Never derive it from a role. */
  can_review: boolean;
};

/** INV-04: one product an order sold but could not fully dispatch. */
export type StockShortfallLine = {
  product_id: number;
  product_name: string;
  ordered: number;
  fulfilled: number;
  shortfall: number;
};

export type AdminOrderDetail = AdminOrder & {
  discount_amount: string;
  coupon_code: string;
  // Phase 4.1 email flags (admin only)
  confirmation_email_sent_at: string | null;
  internal_notification_sent_at: string | null;
  email_send_error: string;
  // Phase 4.0 commercial fields
  customer_phone: string;
  document_type: string;
  document_number: string;
  delivery_method: string;
  address_line: string;
  city: string;
  district: string;
  reference: string;
  notes: string;
  receipt_type: string;
  accepted_terms: boolean;
  accepted_warranty_policy: boolean;
  items: AdminOrderItem[];
  /**
   * Fulfilment states THIS caller may set on this order, decided by the server
   * for this company (H4.1.2). Empty = read-only. Never derive it from the role.
   */
  available_fulfillment_transitions: string[];
  /** INV-04: per-product shortfall for this order (empty when fully covered). */
  stock_shortfall: StockShortfallLine[];
  /**
   * Whether THIS caller may reprocess the pending stock exit: a shortfall to
   * resolve plus the inventory-move authority. Decided by the server for this
   * company. Never derive it from the role.
   */
  can_reprocess_stock_exit: boolean;
  /** Only on a reprocess response: movements this call created. */
  created_movements?: number;
  /**
   * SERIAL-PICK-01: equipos con serie que todavía nadie identificó. Mientras
   * no sea 0 el servidor rechaza «enviado» y «entregado».
   */
  units_pending?: number;
  /** Si QUIEN CONSULTA puede identificar o quitar equipos. Lo decide el servidor. */
  units_can_change?: boolean;
  /** CLIENT-SEC-01: the assessment of this order, or null when it has none. */
  risk?: OrderRisk | null;
  /** DELIVERY-01: the order's delivery record (or its empty shape); null for whoever may not see orders. */
  delivery?: import("./delivery").OrderDelivery | null;
  /** DC3: si QUIEN CONSULTA puede confirmar o dar por fallido el abastecimiento. Lo decide el servidor. */
  can_manage_supply?: boolean;
  /** DC3: si QUIEN CONSULTA puede recibir del proveedor (entra stock). Lo decide el servidor. */
  can_receive_supply?: boolean;
};

/** DC3 — el abastecimiento de un pedido, en palabras del panel. */
export const SUPPLY_STATUS_LABELS: Record<string, string> = {
  awaiting_payment: 'Esperando el pago',
  pending: 'Pedido al proveedor',
  to_confirm: 'Por confirmar con el proveedor',
  received: 'Recibido del proveedor',
  failed: 'No se pudo abastecer',
};

export const PAYMENT_STATUS_LABELS: Record<string, string> = {
  pending_payment: 'Pendiente de pago',
  paid: 'Pagado',
  failed: 'Fallido',
  cancelled: 'Cancelado',
  expired: 'Expirado',
  refunded: 'Reembolsado',
};

export const FULFILLMENT_STATUS_LABELS: Record<string, string> = {
  pending: 'Pendiente',
  confirmed: 'Confirmado',
  preparing: 'En preparación',
  ready_for_pickup: 'Listo para retiro',
  shipped: 'Enviado',
  delivered: 'Entregado',
  cancelled: 'Cancelado operativo',
};

export const FULFILLMENT_STATUS_OPTIONS = Object.entries(FULFILLMENT_STATUS_LABELS).map(
  ([value, label]) => ({ value, label }),
);

export async function fetchAdminOrders(params?: {
  search?: string;
  status?: string;
  fulfillment_status?: string;
  paid?: 'true' | 'false';
  shortfall?: 'true';
  /** CLIENT-SEC-01: only the orders waiting for a person to review them. */
  review?: 'pending';
  /** DC3: por estado de abastecimiento de lo vendido bajo pedido. */
  supply?: 'to_confirm' | 'pending' | 'failed' | 'received';
  date_from?: string;
  date_to?: string;
  page?: number;
  page_size?: number;
}): Promise<PaginatedResponse<AdminOrder>> {
  const qs = buildQs({
    search: params?.search,
    status: params?.status,
    fulfillment_status: params?.fulfillment_status,
    paid: params?.paid,
    shortfall: params?.shortfall,
    review: params?.review,
    supply: params?.supply,
    date_from: params?.date_from,
    date_to: params?.date_to,
    page: params?.page,
    page_size: params?.page_size,
  });
  const res = await fetchWithAuth(`${API_BASE}/admin/orders/${qs}`);
  if (res.status === 403) throw new Error('No tienes permisos para ver órdenes.');
  if (res.status === 401) throw new Error('Sesión expirada. Vuelve a iniciar sesión.');
  if (!res.ok) throw new Error('No se pudieron cargar las órdenes.');
  return res.json();
}

export async function fetchAdminOrderDetail(orderId: number): Promise<AdminOrderDetail> {
  const res = await fetchWithAuth(`${API_BASE}/admin/orders/${orderId}/`);
  if (res.status === 403) throw new Error('No tienes permisos.');
  if (res.status === 404) throw new Error('Orden no encontrada.');
  if (!res.ok) throw new Error('No se pudo cargar la orden.');
  return res.json();
}

export async function updateOrderFulfillment(
  orderId: number,
  fulfillmentStatus: string,
  note?: string,
): Promise<AdminOrderDetail> {
  const res = await fetchWithAuth(`${API_BASE}/admin/orders/${orderId}/fulfillment-status/`, {
    method: 'PATCH',
    body: JSON.stringify({ fulfillment_status: fulfillmentStatus, note: note ?? '' }),
  });
  if (res.status === 400) {
    const err = await res.json().catch(() => null);
    const msg = err?.detail ?? (err && typeof err === 'object'
      ? Object.values(err).flat().join(' ')
      : 'Datos inválidos.');
    throw new Error(msg);
  }
  if (res.status === 403) {
    const err = await res.json().catch(() => null);
    throw new Error(err?.detail ?? 'No tienes permisos para cambiar el estado de despacho.');
  }
  if (res.status === 404) throw new Error('Orden no encontrada.');
  if (res.status === 409) {
    // SERIAL-PICK-01: faltan equipos por identificar. El motivo lo dice el servidor.
    const err = await res.json().catch(() => null);
    throw new Error(err?.detail ?? 'Este pedido todavía no se puede marcar así.');
  }
  if (!res.ok) throw new Error('No se pudo actualizar el estado de despacho.');
  return res.json();
}

// ── CLIENT-SEC-01: the review of an order ───────────────────────────────────

/**
 * Decide the review an order was waiting for. Advice: the server changes the
 * assessment and nothing else of the order. It is decided once — a second
 * answer is refused (409) and the reason is the server's.
 */
export async function reviewOrderRisk(
  orderId: number,
  decision: 'approve' | 'reject',
  note: string,
): Promise<OrderRisk> {
  const res = await fetchWithAuth(`${API_BASE}/admin/orders/${orderId}/risk-review/`, {
    method: 'POST',
    body: JSON.stringify({ decision, note }),
  });
  if (res.ok) return (await res.json()).risk as OrderRisk;
  const err = await res.json().catch(() => null);
  if (res.status === 403) throw new Error(err?.detail ?? 'No tienes permisos para revisar pedidos.');
  if (res.status === 404) throw new Error(err?.detail ?? 'Orden no encontrada.');
  if (res.status === 409) throw new Error(err?.detail ?? 'La revisión de este pedido ya no está pendiente.');
  throw new Error(typeof err?.detail === 'string' ? err.detail : 'No se pudo registrar la revisión.');
}

// ── SERIAL-PICK-01: los equipos de un pedido ────────────────────────────────

async function unitRefusal(res: Response, fallback: string): Promise<never> {
  const err = await res.json().catch(() => null);
  throw new Error(typeof err?.detail === 'string' ? err.detail : fallback);
}

/** Qué equipo es ese código, y si puede ser el de este pedido. No cambia nada. */
export async function lookupOrderUnit(
  orderId: number,
  code: string,
  method: 'scanner' | 'manual',
): Promise<UnitDescription> {
  const query = new URLSearchParams({ code, method });
  const res = await fetchWithAuth(`${API_BASE}/admin/orders/${orderId}/units/lookup/?${query}`);
  if (!res.ok) return unitRefusal(res, 'No se pudo comprobar ese equipo.');
  return (await res.json()).unit;
}

/** Registra ese equipo como el de este pedido. Devuelve el pedido como quedó. */
export async function identifyOrderUnit(
  orderId: number,
  code: string,
  method: 'scanner' | 'manual',
): Promise<AdminOrderDetail> {
  const res = await fetchWithAuth(`${API_BASE}/admin/orders/${orderId}/units/`, {
    method: 'POST',
    body: JSON.stringify({ code, method }),
  });
  if (!res.ok) return unitRefusal(res, 'No se pudo registrar ese equipo.');
  return res.json();
}

/** Quita un equipo identificado por error, mientras el pedido no haya salido. */
export async function forgetOrderUnit(orderId: number, recordId: number): Promise<AdminOrderDetail> {
  const res = await fetchWithAuth(`${API_BASE}/admin/orders/${orderId}/units/${recordId}/`, {
    method: 'DELETE',
  });
  if (!res.ok) return unitRefusal(res, 'No se pudo quitar ese equipo.');
  return res.json();
}

export async function resendOrderConfirmationEmail(orderId: number): Promise<{
  detail: string;
  had_pdf_attachment: boolean;
  resent_to: string;
}> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/orders/${orderId}/resend-confirmation-email/`,
    { method: 'POST' },
  );
  if (res.status === 400) {
    const err = await res.json().catch(() => null);
    throw new Error(err?.detail ?? 'La orden no está pagada.');
  }
  if (res.status === 403) throw new Error('No tienes permisos para reenviar emails.');
  if (res.status === 404) throw new Error('Orden no encontrada.');
  if (res.status === 429) throw new Error('Demasiadas solicitudes. Espera un momento e inténtalo de nuevo.');
  if (res.status === 502 || res.status === 500) {
    const err = await res.json().catch(() => null);
    throw new Error(err?.detail ?? 'Error al enviar el email. Verifica la configuración SMTP.');
  }
  if (!res.ok) throw new Error('No se pudo reenviar el email.');
  return res.json();
}

export async function reprocessOrderStockExit(
  orderId: number,
): Promise<AdminOrderDetail> {
  const res = await fetchWithAuth(
    `${API_BASE}/admin/orders/${orderId}/reprocess-stock-exit/`,
    { method: 'POST' },
  );
  if (res.status === 403) {
    throw new Error('No tienes permisos para reprocesar la salida de stock.');
  }
  if (res.status === 404) throw new Error('Orden no encontrada.');
  if (res.status === 409) {
    const err = await res.json().catch(() => null);
    throw new Error(err?.detail ?? 'Solo un pedido pagado puede reprocesar su salida de stock.');
  }
  if (res.status === 429) {
    throw new Error('Demasiadas solicitudes. Espera un momento e inténtalo de nuevo.');
  }
  if (!res.ok) throw new Error('No se pudo reprocesar la salida de stock.');
  return res.json();
}

export async function downloadOrderReceiptPdf(
  orderId: number,
): Promise<{ blob: Blob; filename: string }> {
  const res = await fetchWithAuth(`${API_BASE}/admin/orders/${orderId}/receipt-pdf/`);
  if (res.status === 400) {
    const err = await res.json().catch(() => null);
    throw new Error(err?.detail ?? 'La orden no está pagada.');
  }
  if (res.status === 403) throw new Error('No tienes permisos para descargar el PDF.');
  if (res.status === 404) throw new Error('Orden no encontrada.');
  if (res.status === 500) {
    const err = await res.json().catch(() => null);
    throw new Error(err?.detail ?? 'Error al generar el PDF.');
  }
  if (!res.ok) throw new Error('No se pudo descargar el PDF.');
  const blob = await res.blob();
  return { blob, filename: `pedido-${orderId}.pdf` };
}

export const ACTION_LABELS: Record<string, string> = {
  role_change: 'Cambio de rol',
  product_created: 'Producto creado',
  product_updated: 'Producto actualizado',
  product_deactivated: 'Producto desactivado',
  product_reactivated: 'Producto reactivado',
  product_inventory_adjusted: 'Inventario ajustado',
  category_created: 'Categoría creada',
  order_fulfillment_status_changed: 'Estado de despacho actualizado',
  order_receipt_pdf_downloaded: 'PDF de recibo descargado',
  order_confirmation_email_resent: 'Email de confirmación reenviado',
  order_stock_exit_reprocessed: 'Salida de stock reprocesada',
};

export function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action;
}

export function formatAdminDate(iso: string): string {
  return new Date(iso).toLocaleString('es-PE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export const ALL_ROLES = [
  { value: 'customer', label: 'Cliente' },
  { value: 'sales', label: 'Vendedor' },
  { value: 'inventory', label: 'Inventario' },
  { value: 'technician', label: 'Técnico' },
  { value: 'admin', label: 'Administrador' },
  { value: 'superadmin', label: 'Superadministrador' },
];
