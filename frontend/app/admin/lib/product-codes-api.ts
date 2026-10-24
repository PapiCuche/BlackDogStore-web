/**
 * LABEL-01 — los códigos de un producto (`/api/admin/products/<id>/barcodes/`).
 *
 * El navegador nunca inventa un código: sólo lee los del servidor, envía el que
 * escribe la persona tal cual y pide `generate/`, que el servidor resuelve sólo
 * si el producto no tiene ninguno. La empresa viaja como `?company=`, que el
 * servidor trata como una selección, nunca como autorización.
 */
import { fetchWithAuth } from "../../lib/auth";
import { API_BASE } from "../../lib/api";

export type ProductCode = {
  id: number;
  code: string;
  symbology: string;
  symbology_label: string;
  is_primary: boolean;
  is_active: boolean;
};

export type ProductCodesList = { results: ProductCode[]; can_manage: boolean };

export class ProductCodeError extends Error {
  constructor(message: string, readonly code: string | null, readonly status: number) {
    super(message);
    this.name = "ProductCodeError";
  }
}

const url = (productId: number, companyId: number | null | undefined, tail = "") =>
  `${API_BASE}/admin/products/${productId}/barcodes/${tail}` +
  (companyId ? `?company=${encodeURIComponent(String(companyId))}` : "");

async function handle<T>(res: Response, fallback: string): Promise<T> {
  const data = await res.json().catch(() => ({}));
  if (res.ok) return data as T;
  const body = (data ?? {}) as { detail?: unknown; code?: unknown };
  const fieldMessage = Array.isArray(body.code) ? String(body.code[0] ?? "") : "";
  const message = typeof body.detail === "string" && body.detail ? body.detail : fieldMessage || fallback;
  throw new ProductCodeError(message, typeof body.code === "string" ? body.code : null, res.status);
}

export async function fetchProductCodes(productId: number, companyId?: number | null): Promise<ProductCodesList> {
  return handle(await fetchWithAuth(url(productId, companyId)), "No se pudieron cargar los códigos.");
}

export async function addProductCode(
  productId: number, code: string, companyId?: number | null,
): Promise<{ results: ProductCode[]; warnings?: string[] }> {
  return handle(
    await fetchWithAuth(url(productId, companyId), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    }),
    "No se pudo agregar el código.",
  );
}

export async function generateProductCode(
  productId: number, companyId?: number | null,
): Promise<{ generated: boolean; code: string | null; results: ProductCode[] }> {
  return handle(
    await fetchWithAuth(url(productId, companyId, "generate/"), { method: "POST" }),
    "No se pudo generar el código.",
  );
}

/** Un código por línea, el principal primero; los desactivados no se descargan. */
export function codesAsText(codes: ProductCode[]): string {
  return codes.filter((c) => c.is_active).map((c) => c.code).join("\n") + "\n";
}
