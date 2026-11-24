/**
 * NAV-SALES-01 — «Productos › Colores» is the «Atributos y opciones» section of Modelos (where each colour lives,
 * with its swatch), opened with `/admin/products/models?seccion=colores`. The menu entry and the page agree on the
 * name through this file.
 */
export const MODELS_COLOURS_SECTION = "colores";

const ANCHORS: Record<string, string> = { [MODELS_COLOURS_SECTION]: "atributos" };

export function modelsAnchorFor(query: string): string | null {
  const section = new URLSearchParams(query.startsWith("?") ? query.slice(1) : query).get("seccion") ?? "";
  return Object.prototype.hasOwnProperty.call(ANCHORS, section) ? ANCHORS[section] : null;
}
