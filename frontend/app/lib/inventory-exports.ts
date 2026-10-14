import { API_BASE } from "./api";
import { fetchWithAuth } from "./auth";

/**
 * INVENTORY-UNIFIED-01 — las descargas del inventario.
 *
 * Tres libros de una hoja (`products`, `stock`, `units`) y los tres juntos en
 * un ZIP (`full`). Cada libro sale en uno de dos modos, y la diferencia es de
 * fondo: `reimport` es el archivo que las cargas masivas vuelven a leer tal
 * cual; `report` añade columnas de sólo lectura y las cargas lo rechazan.
 *
 * Quién puede descargar qué, de qué sucursales y con qué columnas lo decide el
 * servidor. Aquí sólo se arma la petición y se guarda lo que responde.
 */

export type ExportKind = "products" | "stock" | "units" | "full";
export type ExportMode = "reimport" | "report";

export type ExportFilters = {
  /** Vacío = todas las sucursales a las que la persona tiene acceso. */
  branches?: number[];
  category?: string;
  type?: "" | "serialized" | "quantity";
  stock?: "" | "in_stock" | "out_of_stock";
  condition?: string;
  visibility?: "" | "published" | "internal";
  is_active?: "" | "true" | "false";
};

/** La dirección de una descarga, con sólo los filtros que se pusieron. */
export function inventoryExportPath(
  kind: ExportKind, mode: ExportMode, filters: ExportFilters, companyId: number | null,
): string {
  const qs = new URLSearchParams();
  if (companyId) qs.set("company", String(companyId));
  // El ZIP sólo existe para volver a cargar: no se le pide otro modo.
  if (kind !== "full") qs.set("mode", mode);
  if (filters.branches?.length) qs.set("branches", filters.branches.join(","));
  for (const key of ["category", "type", "stock", "condition", "visibility", "is_active"] as const) {
    const value = filters[key];
    if (value) qs.set(key, value);
  }
  const query = qs.toString();
  return `${API_BASE}/admin/inventory/exports/${kind}/${query ? `?${query}` : ""}`;
}

/** Si el servidor no nombra el archivo. Los mismos nombres que él pone. */
const FALLBACK_NAMES: Record<string, string> = {
  "products:reimport": "productos-para-cargar.xlsx",
  "products:report": "reporte-productos.xlsx",
  "stock:reimport": "stock-para-cargar.xlsx",
  "stock:report": "reporte-stock.xlsx",
  "units:reimport": "equipos-para-cargar.xlsx",
  "units:report": "reporte-equipos.xlsx",
  "full:reimport": "inventario-completo.zip",
};

/** Pide una descarga y la guarda. Lanza un error con lo que el servidor explicó. */
export async function downloadInventoryExport(
  kind: ExportKind, mode: ExportMode, filters: ExportFilters, companyId: number | null,
): Promise<void> {
  const res = await fetchWithAuth(inventoryExportPath(kind, mode, filters, companyId));
  if (!res.ok) {
    const err = await res.json().catch(() => null);
    if (res.status === 403) throw new Error("No tienes permisos para esta descarga.");
    if (res.status === 404) throw new Error("La sucursal o la categoría elegida ya no está disponible. Actualiza la página.");
    if (res.status === 429) {
      // Lo que el servidor dice: que las descargas están limitadas y en cuántos segundos volver.
      if (typeof err?.detail === "string" && err.detail) throw new Error(err.detail);
      const wait = Number(res.headers.get("Retry-After"));
      throw new Error(
        Number.isFinite(wait) && wait > 0
          ? `Las descargas del inventario están limitadas. Vuelve a intentarlo en ${wait} segundos.`
          : "Las descargas del inventario están limitadas. Espera un momento e inténtalo de nuevo.",
      );
    }
    throw new Error(typeof err?.detail === "string" ? err.detail : "No se pudo generar la descarga.");
  }
  // El nombre lo pone el servidor, y es fijo: nada de lo que alguien escribió.
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const served = /filename="([^"]+)"/.exec(disposition)?.[1];
  const effectiveMode = kind === "full" ? "reimport" : mode;

  const url = URL.createObjectURL(await res.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = served ?? FALLBACK_NAMES[`${kind}:${effectiveMode}`];
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/** Las sucursales en las que la persona opera. Para ofrecerlas; la autoridad sigue en el servidor. */
export async function fetchExportBranches(companyId: number | null): Promise<{ id: number; name: string }[]> {
  const qs = companyId ? `?company=${encodeURIComponent(String(companyId))}` : "";
  const res = await fetchWithAuth(`${API_BASE}/admin/inventory/branches/${qs}`);
  if (!res.ok) throw new Error("No se pudieron cargar las sucursales.");
  const body = await res.json();
  return (body.results ?? []).map((branch: { id: number; name: string }) => ({ id: branch.id, name: branch.name }));
}
