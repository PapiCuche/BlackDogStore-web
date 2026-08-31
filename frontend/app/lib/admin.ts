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
  created_at: string;
  updated_at: string;
};

export type AdminCategory = {
  id: number;
  name: string;
  slug: string;
};

export async function fetchAdminProducts(params?: {
  search?: string;
  category?: number | string;
  is_active?: 'true' | 'false';
  stock?: 'in_stock' | 'out_of_stock' | 'low_stock';
  page?: number;
  page_size?: number;
}): Promise<PaginatedResponse<AdminProduct>> {
  const qs = buildQs({
    search: params?.search,
    category: params?.category !== undefined ? String(params.category) : undefined,
    is_active: params?.is_active,
    stock: params?.stock,
    page: params?.page,
    page_size: params?.page_size,
  });
  const res = await fetchWithAuth(`${API_BASE}/admin/products/${qs}`);
  if (res.status === 403) throw new Error('No tienes permisos para ver productos.');
  if (res.status === 401) throw new Error('Sesión expirada. Vuelve a iniciar sesión.');
  if (!res.ok) throw new Error('No se pudieron cargar los productos.');
  return res.json();
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
  if (res.status === 403) throw new Error('No tienes permisos para editar productos.');
  if (res.status === 404) throw new Error('Producto no encontrado.');
  if (!res.ok) throw new Error('No se pudo actualizar el producto.');
  return res.json();
}

export async function adjustInventory(
  productId: number,
  delta: number,
  reason: string,
): Promise<AdminProduct> {
  const res = await fetchWithAuth(`${API_BASE}/admin/products/${productId}/inventory-adjust/`, {
    method: 'POST',
    body: JSON.stringify({ delta, reason }),
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

export async function fetchAdminCategories(): Promise<AdminCategory[]> {
  const res = await fetchWithAuth(`${API_BASE}/admin/categories/`);
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

// ---------------------------------------------------------------------------
// Admin orders types and helpers
// ---------------------------------------------------------------------------

export type AdminOrderItem = {
  id: number;
  product_id: number;
  product_name: string;
  quantity: number;
  price: string;
  subtotal: string;
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
  if (!res.ok) throw new Error('No se pudo actualizar el estado de despacho.');
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
  return { blob, filename: `blackdog-pedido-${orderId}.pdf` };
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
