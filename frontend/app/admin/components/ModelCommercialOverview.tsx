"use client";

/**
 * Productos › Modelos › un modelo › «Configuración comercial» — CATALOG-REFERENCE-01 · PR E.
 *
 * ONE PLACE TO SEE what the shop will show of this model and what is still missing, and a link to the editor that
 * keeps each part. It has no form of its own: visibility and text are the model's form, options the palette, prices
 * the variants, pictures the finish galleries, on-order each variant's sale mode, suppliers their offers and
 * promotions their own screen. It only reads `GET …/models/<id>/commercial/` (`products.view`).
 *
 * A supplier's agreed cost is shown only when the server sent it (`costs.view`) AND the caller may see costs; promotions
 * only when the server sent them (`sales.promotions.view`).
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { fetchCommercialSummary, type CommercialSummary } from "../lib/catalog-presentation-api";
import type { AttributeRole } from "../lib/internal-api";
import { ErrorBox, Panel, Spinner, formatSoles } from "./InventoryUi";
import { internalButtonClass } from "./internal-ui";

const message = (error: unknown, fallback: string) => (error instanceof Error ? error.message : fallback);

const ROLE_WORDS: Record<AttributeRole, [string, string]> = {
  capacity: ["capacidad", "capacidades"],
  color: ["color", "colores"],
  sim: ["SIM", "SIM"],
  region: ["región", "regiones"],
  other: ["otra opción", "otras opciones"],
};

const count = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

type Tone = "ok" | "todo" | "info";

const TONE: Record<Tone, string> = {
  ok: "border-success-border text-success",
  todo: "border-warning-border bg-warning-surface text-warning",
  info: "border-bd-border text-muted",
};

const TONE_WORD: Record<Tone, string> = { ok: "Listo", todo: "Falta", info: "Info" };

function Row({
  title, tone, children, href, action,
}: { title: string; tone: Tone; children: React.ReactNode; href: string; action: string }) {
  return (
    <li className="flex min-w-0 flex-col gap-2 border-b border-bd-border py-3 last:border-b-0 sm:flex-row sm:items-center">
      <div className="flex min-w-0 flex-1 items-start gap-3">
        <span className={`mt-0.5 inline-flex w-14 shrink-0 justify-center rounded-full border px-2 py-0.5 text-[11px] font-semibold ${TONE[tone]}`}>
          {TONE_WORD[tone]}
        </span>
        <div className="min-w-0">
          <p className="text-sm font-semibold text-foreground">{title}</p>
          <div className="mt-0.5 break-words text-xs text-muted">{children}</div>
        </div>
      </div>
      <Link
        href={href}
        className="inline-flex min-h-[44px] shrink-0 items-center self-start rounded-lg border border-bd-border px-3 text-xs font-semibold text-foreground transition-colors hover:border-foreground/25 sm:self-center"
        aria-label={`${action}: ${title}`}
      >
        {action}
      </Link>
    </li>
  );
}

export function ModelCommercialOverview({
  modelId, companyId, canSeeCosts, refreshKey,
}: { modelId: number; companyId: number | null; canSeeCosts: boolean; refreshKey?: unknown }) {
  const [summary, setSummary] = useState<CommercialSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchCommercialSummary(modelId, companyId).then((data) => { setSummary(data); setError(null); }, (err) => setError(message(err, "No se pudo leer la configuración comercial.")));
  }, [modelId, companyId]);

  useEffect(() => { load(); }, [load, refreshKey]);

  return (
    <Panel
      id="configuracion-comercial"
      title="Configuración comercial"
      description="Lo que la tienda mostrará de este modelo y lo que aún falta. Cada parte se edita en su propia sección."
    >
      {error ? (
        <div className="space-y-2">
          <ErrorBox message={error} />
          <button type="button" className={`min-h-[44px] ${internalButtonClass}`} onClick={load}>Reintentar</button>
        </div>
      ) : summary === null ? <Spinner label="Leyendo la configuración comercial…" /> : (
        <SummaryRows summary={summary} canSeeCosts={canSeeCosts} />
      )}
    </Panel>
  );
}

export function SummaryRows({ summary, canSeeCosts }: { summary: CommercialSummary; canSeeCosts: boolean }) {
  const { visibility, content, palette, variants, finishes, suppliers, promotions } = summary;
  const options = (Object.entries(palette.options) as [AttributeRole, number][])
    .map(([role, n]) => count(n, ...ROLE_WORDS[role] ?? [role, role]));
  return (
    <ul aria-label="Configuración comercial">
      <Row title="Visibilidad en la tienda" tone={visibility.is_published_online ? "ok" : "info"} href="#modelo" action="Ir a los datos del modelo">
        {visibility.is_published_online ? "Publicado" : "Oculto"}
        {visibility.is_featured ? " · destacado (va primero en la lista)" : " · no destacado"}
        {` · orden ${visibility.sort_order}`}
      </Row>
      <Row title="Texto comercial y descripción" tone={content.has_description ? "ok" : "todo"} href="#modelo" action="Ir a la descripción">
        {content.has_description ? "Tiene descripción con sus características." : "Sin descripción: escribe el texto comercial y las características."}
        {content.has_cover ? " Tiene portada para las listas." : " Sin portada para las listas."}
      </Row>
      <Row title="Capacidades, colores, SIM y regiones" tone={palette.has_palette ? "ok" : "todo"} href="#paleta" action="Ir a las opciones">
        {palette.has_palette ? options.join(" · ") : "Sin paleta: marca qué opciones admite el modelo."}
        {palette.exclusions ? ` · ${count(palette.exclusions, "combinación que no existe", "combinaciones que no existen")}` : ""}
      </Row>
      <Row title="Orden de los colores" tone="info" href="#orden-opciones" action="Ir al orden">
        Cómo se ven los colores y demás opciones en la página del modelo.
      </Row>
      <Row
        title="Variantes y precio por variante"
        tone={variants.total === 0 || variants.priced < variants.new || variants.pending_drafts ? "todo" : "ok"}
        href="#variantes"
        action="Ir a las variantes"
      >
        {variants.total === 0 ? "Sin variantes: créalas desde la matriz de opciones." : (
          <>
            {count(variants.total, "variante", "variantes")} ({variants.new} nuevas, {variants.used} seminuevas)
            {` · ${variants.priced} de ${variants.new} nuevas con precio`}
            {` · ${count(variants.published, "publicada", "publicadas")}`}
            {variants.pending_drafts ? ` · ${count(variants.pending_drafts, "borrador por habilitar", "borradores por habilitar")}` : ""}
          </>
        )}
      </Row>
      <Row title="Imágenes por acabado" tone={finishes.colours && !finishes.pending ? "ok" : "todo"} href="#acabados" action="Ir a las imágenes">
        {finishes.colours === 0 ? "Sin colores todavía."
          : finishes.pending ? `${count(finishes.colours, "color", "colores")} · ${finishes.pending} con «Imagen pendiente»`
            : `${count(finishes.colours, "color", "colores")}, todos con imagen`}
      </Row>
      <Row title="Bajo pedido" tone="info" href="#variantes" action="Ir a las variantes">
        {variants.on_order
          ? `${count(variants.on_order, "variante admite", "variantes admiten")} venta bajo pedido; se cambia en la ficha de cada variante.`
          : "Ninguna variante se vende bajo pedido; se activa en la ficha de cada variante."}
      </Row>
      <Row title="Proveedores asociados" tone="info" href="/admin/products/suppliers/offers" action="Ir a las ofertas">
        {suppliers.length === 0 ? "Ninguna oferta de proveedor activa para sus variantes." : (
          <ul className="space-y-0.5">
            {suppliers.map((supplier) => (
              <li key={supplier.id}>
                <span className="text-foreground">{supplier.name}</span>
                {` · ${count(supplier.offers, "oferta", "ofertas")} para ${count(supplier.variants, "variante", "variantes")}`}
                {canSeeCosts && supplier.lowest_agreed_cost ? ` · costo acordado desde ${formatSoles(supplier.lowest_agreed_cost)}` : ""}
              </li>
            ))}
          </ul>
        )}
      </Row>
      <Row title="Promociones aplicables" tone="info" href="/admin/sales/promotions" action="Ir a las promociones">
        {promotions === null ? "No tienes permiso para ver promociones." : promotions.length === 0
          ? "Ninguna promoción incluye sus variantes." : (
            <ul className="space-y-0.5">
              {promotions.map((promo) => (
                <li key={promo.id}><span className="text-foreground">{promo.name}</span>{promo.is_live ? " · vigente" : " · no vigente"}</li>
              ))}
            </ul>
          )}
      </Row>
      {content.reference_key ? (
        <Row title="Catálogo de referencia" tone="info" href="/admin/products/models/reference" action="Ir al catálogo">
          Importado del catálogo de referencia de fabricantes.
        </Row>
      ) : null}
    </ul>
  );
}
