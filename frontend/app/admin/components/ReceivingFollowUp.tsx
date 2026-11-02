"use client";

/**
 * INV-RECEIVE-PRICE-03 — «Seguimiento» de cada línea recibida (paso 4 del asistente).
 *
 * Lo calcula el servidor al responder (también al repetir una confirmación): cuántos equipos tienen precio y cuántos
 * no, qué le falta a su ficha, dónde quedó y desde dónde despacha la tienda en línea, cómo está su publicación y si
 * la tienda lo ofrece ya. Sólo conteos y códigos: ni importes, ni costos, ni identificadores. Nada de esto publica:
 * cada «siguiente paso» lleva a la pantalla donde una persona con permiso lo decide.
 */

import Link from "next/link";

import type { ReceiptFollowUp, ReceiptResult } from "../lib/inventory-receipts";
import { usedUnitsHref } from "../lib/used-units-access";
import { internalButtonClass } from "./internal-ui";

type ResultLine = ReceiptResult["lines"][number];

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

const PROBLEM_TEXT: Record<string, string> = {
  inactive: "activar el artículo",
  no_price: "su precio de venta",
  no_image: "una imagen del artículo",
  no_category: "una categoría",
};

/** Why the online shop does not offer this line, in the words of the screen. */
export const BLOCKED_TEXT: Record<string, string> = {
  no_dispatch_branch: "la empresa no tiene una sucursal de despacho para la tienda en línea",
  not_in_dispatch_branch: "lo recibido no está en la sucursal de despacho",
  not_published: "no está publicado: hoy es stock interno",
  model_hidden: "su modelo está oculto en la tienda",
  unpriced: "ningún equipo tiene precio de venta",
  not_available: "sus equipos ya no están disponibles",
};

export function publicationText(info: ReceiptFollowUp): string {
  if (info.state === "published") return "Publicado.";
  const missing = info.problems.map((code) => PROBLEM_TEXT[code]).filter(Boolean);
  if (info.state === "ready") return "Preparado para publicar: publicarlo es una decisión aparte, en «Publicación e-commerce».";
  return `Pendiente de completar para publicar${missing.length ? `: falta ${missing.join(", ")}` : ""}.`;
}

export function onlineText(info: ReceiptFollowUp): string {
  if (info.online === "offered") {
    return info.units_offered_online === null
      ? "La tienda en línea lo ofrece ahora."
      : `La tienda en línea ofrece ahora ${plural(info.units_offered_online, "equipo", "equipos")} de esta línea.`;
  }
  const reasons = info.blocked_reasons.map((code) => BLOCKED_TEXT[code] ?? code);
  return `La tienda en línea no lo ofrece todavía: ${reasons.join("; ")}.`;
}

export function dispatchText(info: ReceiptFollowUp): string {
  if (!info.dispatch_branch) return "La empresa no tiene una sucursal de despacho definida: la tienda en línea no puede ofrecer nada.";
  if (info.in_dispatch_branch) return `Se despacha en línea desde ${info.dispatch_branch.name}, la misma sucursal donde quedó.`;
  return `La tienda en línea despacha desde ${info.dispatch_branch.name}: lo recibido ${info.storage_branch ? `en ${info.storage_branch.name} ` : ""}no se ofrece en línea hasta que esté allí.`;
}

export function missingDetailsText(info: ReceiptFollowUp): string | null {
  const parts = [
    info.units_without_battery ? `${plural(info.units_without_battery, "equipo", "equipos")} sin batería medida` : "",
    info.units_without_grade ? `${plural(info.units_without_grade, "equipo", "equipos")} sin estado estético` : "",
    info.units_without_photos ? `${plural(info.units_without_photos, "equipo", "equipos")} sin fotos propias` : "",
  ].filter(Boolean);
  return parts.length ? `Ficha por completar (recomendado para publicar): ${parts.join(", ")}.` : null;
}

export function ReceivingFollowUp({
  line, canManagePrices, canViewProducts,
}: { line: ResultLine; canManagePrices: boolean; canViewProducts: boolean }) {
  const info = line.follow_up;
  if (!info) return null;
  const byUnit = info.priced_by === "unit";
  const missing = byUnit ? missingDetailsText(info) : null;
  const received = line.kind === "units"
    ? plural(info.received, "equipo identificado", "equipos identificados")
    : plural(info.received, "unidad", "unidades");
  return (
    <article aria-label={`Seguimiento de ${line.product.name}`} className="space-y-2 rounded-xl border border-bd-border bg-surface p-4 text-sm">
      <h4 className="break-words font-semibold text-foreground">{line.product.name}</h4>
      <ul className="space-y-1 text-foreground">
        <li>Recibido: {received}{info.storage_branch ? ` en ${info.storage_branch.name}` : ""}.</li>
        {byUnit ? (
          <li className={info.units_unpriced ? "text-warning" : undefined}>
            {info.units_unpriced
              ? `Precio de venta: ${plural(info.units_priced, "equipo con precio", "equipos con precio")} y ${plural(info.units_unpriced, "pendiente", "pendientes")}. Un equipo pendiente no se vende en caja ni se ofrece en línea hasta configurarlo.`
              : `Precio de venta: ${info.units_priced === 1 ? "el equipo tiene" : `los ${info.units_priced} equipos tienen`} su precio.`}
          </li>
        ) : null}
        {missing ? <li className="text-muted">{missing}</li> : null}
        <li className={info.in_dispatch_branch ? undefined : "text-warning"}>{dispatchText(info)}</li>
        <li>
          Publicación: {publicationText(info)}
          {info.model && !info.model.published ? ` El modelo «${info.model.name}» está oculto.` : ""}
        </li>
        <li className={info.online === "offered" ? "text-success" : "text-muted"}>{onlineText(info)}</li>
      </ul>

      <div className="flex flex-wrap gap-2 pt-1">
        {byUnit && info.units_unpriced > 0 && canManagePrices ? (
          <Link href={usedUnitsHref(line.product.id, true)} className={`${internalButtonClass} inline-flex min-h-11 items-center`}>
            {`Fijar el precio de ${plural(info.units_unpriced, "equipo", "equipos")}`}
          </Link>
        ) : null}
        {byUnit && missing ? (
          <Link href={usedUnitsHref(line.product.id)} className={`${internalButtonClass} inline-flex min-h-11 items-center`}>
            Completar datos y fotos
          </Link>
        ) : null}
        {info.state !== "published" && canViewProducts ? (
          <Link
            href={`/admin/products/publication?search=${encodeURIComponent(line.product.name)}`}
            className={`${internalButtonClass} inline-flex min-h-11 items-center`}
          >
            Revisar la publicación
          </Link>
        ) : null}
      </div>
      {byUnit && info.units_unpriced > 0 && !canManagePrices ? (
        <p className="text-xs text-muted">Tu usuario no puede fijar precios: pide a quien gestiona productos que lo haga en «Equipos seminuevos».</p>
      ) : null}
      {byUnit && info.units_unpriced > 0 ? (
        <p className="text-xs text-muted">Poner un precio no publica nada: publicar sigue siendo una decisión aparte.</p>
      ) : null}
    </article>
  );
}
