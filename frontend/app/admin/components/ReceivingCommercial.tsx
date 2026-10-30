"use client";

/**
 * INV-COM-01B — «Destino comercial» de una línea de la recepción: dónde queda en la tienda y qué falta.
 *
 * Es una lectura (el servidor la calcula con las reglas reales de publicación y no repite ningún precio): una
 * recepción no publica, no activa ni cambia el precio de nada. La cadena es la del catálogo:
 * Modelo → Línea → Variante → Unidades identificadas. «Publicado» no promete que el cliente lo vea: eso además
 * depende de las existencias en la sucursal de despacho y, en seminuevos, de que cada equipo tenga su precio.
 */

import type { Commercial, ReceiptPreview } from "../lib/inventory-receipts";

type PreviewLine = ReceiptPreview["lines"][number];

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** What `product_publication` can say is missing, in the words of the screen. */
const PROBLEM_TEXT: Record<string, string> = {
  inactive: "activar el artículo",
  no_price: "su precio de venta",
  no_image: "una imagen",
  no_category: "una categoría",
};

export function destinationChain(line: PreviewLine, commercial: Commercial): string[] {
  const lineName = line.product.condition_line === "used" ? "Seminuevos" : "Nuevos";
  const count = line.kind === "units"
    ? plural(line.will_receive || 0, "unidad identificada", "unidades identificadas")
    : plural(line.will_receive || 0, "unidad por cantidad", "unidades por cantidad");
  return commercial.model
    ? [commercial.model.name, lineName, line.product.name, count]
    : [line.product.name, count];
}

export function destinationState(commercial: Commercial): string {
  if (commercial.state === "published") {
    return "Publicado. Que un cliente lo vea además depende de las existencias de la sucursal de despacho"
      + (commercial.priced_by === "unit" ? " y de que cada equipo tenga su precio." : ".");
  }
  if (commercial.state === "ready") {
    return "Preparado para publicar. Hoy es stock interno: publicarlo es una decisión aparte, en «Publicación e-commerce».";
  }
  const missing = commercial.problems.map((code) => PROBLEM_TEXT[code]).filter(Boolean);
  const parts = [...missing];
  if (commercial.units_unpriced > 0) {
    parts.push(`el precio propio de ${plural(commercial.units_unpriced, "equipo", "equipos")}`);
  }
  return `Pendiente de completar para publicar${parts.length ? `: falta ${parts.join(", ")}` : ""}. Mientras tanto es stock interno.`;
}

export function ReceivingCommercial({ line, canViewProducts }: { line: PreviewLine; canViewProducts: boolean }) {
  const commercial = line.commercial;
  if (!commercial) return null;
  const chain = destinationChain(line, commercial);
  const hiddenModel = commercial.placement === "model_page" && commercial.model && !commercial.model.published;
  return (
    <section aria-label={`Destino comercial de ${line.product.name}`} className="mt-2 rounded-lg border border-bd-border bg-background p-3 text-xs">
      <p className="font-semibold text-foreground">Destino comercial</p>
      <p className="mt-1 break-words text-foreground" aria-label="Del modelo a la unidad">{chain.join(" → ")}</p>
      <p className="mt-1 text-muted">
        {commercial.placement === "model_page"
          ? "Cada equipo se ofrece, uno por uno, en la página de su modelo; no tiene una tarjeta pública propia."
          : "Tiene su tarjeta propia en la tienda cuando se publica."}
      </p>
      <p className={`mt-1 ${commercial.state === "pending" ? "text-warning" : "text-foreground"}`}>{destinationState(commercial)}</p>
      {!commercial.has_dispatch_branch ? (
        <p className="mt-1 text-warning">La empresa no tiene una sucursal de despacho definida: la tienda en línea no puede ofrecer nada hasta que se defina.</p>
      ) : !commercial.in_dispatch_branch ? (
        <p className="mt-1 text-warning">Esta sucursal no es la de despacho de la tienda en línea: lo que recibes aquí no se ofrece en línea hasta que esté en la sucursal de despacho.</p>
      ) : null}
      {hiddenModel ? (
        <p className="mt-1 text-warning">El modelo «{commercial.model!.name}» está oculto: su página no se muestra aunque la variante esté publicada.</p>
      ) : null}
      {canViewProducts && commercial.state !== "published" ? (
        <a
          href="/admin/products/publication" target="_blank" rel="noopener noreferrer" data-receiving-leave="ok"
          className="mt-2 inline-flex min-h-11 items-center font-semibold text-foreground underline"
        >
          Abrir Publicación e-commerce (se abre en otra pestaña)
        </a>
      ) : null}
    </section>
  );
}
