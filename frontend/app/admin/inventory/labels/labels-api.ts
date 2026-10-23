/**
 * LABEL-03 — «Etiquetas y códigos de barras»: vista previa y PDF a escala real.
 *
 * Sólo lectura. El servidor resuelve la empresa y la sucursal; lo que viaja aquí
 * es una SELECCIÓN (ids y copias) que él vuelve a validar. Descargar el PDF no
 * marca nada como impreso ni encola nada.
 */
import { fetchWithAuth } from "../../../lib/auth";
import { API_BASE } from "../../../lib/api";

export type LabelSelection = {
  branch: number;
  products: { id: number; copies: number }[];
  units: number[];
};

export type LabelRow = {
  kind: "accessory" | "device";
  ref: number;
  title: string;
  status: "ready" | "review";
  reasons: string[];
  warnings: string[];
  copies: number;
  stickers: number;
  pages: number;
  layout: { key: string; name: string; width_mm: number; height_mm: number };
  code: {
    value: string; symbology: string; kind: string; width_mm: number; module_mm: number; module_dots: number;
  } | null;
  lines: string[];
  has_logo: boolean;
  sensitive: boolean;
};

export type LabelPreview = {
  labels: LabelRow[];
  summary: { ready: number; review: number; pages: number };
  permissions: { sensitive: boolean; device_profile: string };
};

export const MAX_COPIES = 500;

export function labelsQuery(selection: LabelSelection): string {
  const qs = new URLSearchParams();
  qs.set("branch", String(selection.branch));
  qs.set("products", selection.products.map((p) => `${p.id}:${p.copies}`).join(","));
  qs.set("units", selection.units.join(","));
  return `?${qs.toString()}`;
}

async function detail(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => null);
  return body && typeof body.detail === "string" ? body.detail : fallback;
}

export async function previewLabels(selection: LabelSelection): Promise<LabelPreview> {
  const res = await fetchWithAuth(`${API_BASE}/admin/inventory/labels/preview/${labelsQuery(selection)}`);
  if (!res.ok) {
    throw new Error(await detail(res, res.status === 403
      ? "No tienes permiso para ver etiquetas."
      : "No se pudo preparar la vista previa."));
  }
  return res.json();
}

export type LabelPdf = { blob: Blob; pages: number; review: number };

/** El PDF de las etiquetas listas. Con todas en revisión, el servidor responde 409 con sus motivos. */
export async function fetchLabelsPdf(selection: LabelSelection): Promise<LabelPdf> {
  const res = await fetchWithAuth(`${API_BASE}/admin/inventory/labels/pdf/${labelsQuery(selection)}`);
  if (!res.ok) {
    throw new Error(await detail(res, "No se pudo generar el PDF."));
  }
  return {
    blob: await res.blob(),
    pages: Number(res.headers.get("X-Labels-Pages") ?? 0),
    review: Number(res.headers.get("X-Labels-Review") ?? 0),
  };
}
