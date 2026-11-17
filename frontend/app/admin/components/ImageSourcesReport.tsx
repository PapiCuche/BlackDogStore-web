"use client";

/**
 * Productos › «Imágenes por reemplazar» — P2 · F4 «Imágenes propias».
 *
 * Las fichas del catálogo cuyas imágenes no subió la tienda: fotos enlazadas desde el CDN de un fabricante o de
 * un proveedor. El manual de marca pide fotos propias y una foto ajena es un riesgo de licencia; la tienda ya las
 * rotula «Imagen referencial». Aquí se ve qué reemplazar y se va a editarlo.
 *
 * Sólo lectura. Qué es externo, de qué empresa y hasta cuántas filas por página lo decide el servidor
 * (`GET /admin/catalog/image-sources/`, `products.view`). Subir en lote exige `products.manage`: sin él no se
 * ofrece un enlace que respondería 403.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { fetchImageSources, type ImageSourceKind, type ImageSourcesReport as Report } from "../lib/internal-api";
import { EmptyBox, Panel, Spinner } from "./InventoryUi";
import { internalButtonClass } from "./internal-ui";

const PAGE_SIZE = 50;
const BULK_IMAGES_HREF = "/admin/inventory/bulk#imagenes";

const KIND_LABEL: Record<ImageSourceKind, string> = { model: "Modelo", variant: "Variante", product: "Producto" };

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;
const images = (count: number) => plural(count, "imagen", "imágenes");
const message = (error: unknown) =>
  error instanceof Error && error.message ? error.message : "No se pudo cargar el informe de imágenes.";

/** «1 modelo, 2 variantes y 3 productos»: sólo lo que hay. */
function itemsSentence(summary: Report["summary"]): string {
  const parts = [
    summary.models ? plural(summary.models, "modelo", "modelos") : "",
    summary.variants ? plural(summary.variants, "variante", "variantes") : "",
    summary.products ? plural(summary.products, "producto", "productos") : "",
  ].filter(Boolean);
  const list = parts.length > 1 ? `${parts.slice(0, -1).join(", ")} y ${parts.at(-1)}` : parts[0] ?? "";
  const verb = summary.items === 1 ? "usa" : "usan";
  return `${list} ${verb} imágenes que la tienda no subió (${images(summary.images)}).`;
}

/** The server's «host» for a path of this site that is not an upload (`SITE_PATH` in `catalog_image_sources.py`). */
const SITE_PATH = "(ruta del sitio)";
const hostLabel = (host: string) =>
  host === SITE_PATH ? "Rutas de este sitio que no son subidas desde el panel" : host || "(sin dominio)";
const rowHostLabel = (host: string) => (host === SITE_PATH ? "Ruta de este sitio" : host || "(sin dominio)");

const touch = "min-h-[44px]";
const badge = "inline-flex rounded-full border px-2 py-0.5 text-[11px] font-semibold";

export function ImageSourcesReport({ companyId, canManage }: { companyId: number | null; canManage: boolean }) {
  const [page, setPage] = useState(1);
  // «Reintentar» asks again for the same page: a new attempt is a new request.
  const [attempt, setAttempt] = useState(0);
  const key = `${companyId ?? ""}:${page}:${attempt}`;
  // What the server answered last, and for which request: anything else is still loading.
  const [answer, setAnswer] = useState<{ key: string; data: Report | null; error: string | null } | null>(null);

  useEffect(() => {
    let current = true;
    fetchImageSources({ page, pageSize: PAGE_SIZE }, companyId).then(
      (data) => { if (current) setAnswer({ key, data, error: null }); },
      (err) => { if (current) setAnswer((last) => ({ key, data: last?.data ?? null, error: message(err) })); },
    );
    return () => { current = false; };
  }, [companyId, page, key]);

  const loading = answer?.key !== key;
  const error = loading ? null : answer?.error ?? null;
  const data = answer?.data ?? null;

  const pages = data ? Math.max(1, Math.ceil(data.count / Math.max(1, data.page_size))) : 1;

  const bulk = (
    <div className="rounded-xl border border-bd-border px-4 py-3 text-sm text-foreground">
      <p>
        La portada de un modelo se cambia en la página del modelo. Las fotos de productos y variantes también se
        pueden subir muchas a la vez: cada archivo va a su producto por el código.
      </p>
      {canManage ? (
        <Link href={BULK_IMAGES_HREF} className={`${touch} ${internalButtonClass} mt-3`}>Subir imágenes en lote</Link>
      ) : (
        <p className="mt-2 text-xs text-muted">
          Subir imágenes en lote exige permiso para administrar productos. Pídeselo a quien administra el catálogo.
        </p>
      )}
    </div>
  );

  if (error) {
    return (
      <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-danger-border bg-danger-surface px-4 py-3">
        <p className="text-sm text-danger">{error}</p>
        <button type="button" className={`${touch} ${internalButtonClass}`} onClick={() => setAttempt((n) => n + 1)}>Reintentar</button>
      </div>
    );
  }

  if (data === null) {
    return (
      <div aria-busy="true">
        <Spinner label="Cargando informe de imágenes…" />
      </div>
    );
  }

  const { summary } = data;

  if (summary.items === 0) {
    return (
      <div className="space-y-6">
        <div role="status">
          <EmptyBox message="Todas las imágenes del catálogo son propias: las subió la tienda. No hay nada que reemplazar." />
        </div>
        {bulk}
      </div>
    );
  }

  return (
    <div className="space-y-6" aria-busy={loading}>
      <Panel id="resumen" title="Resumen" description="Imágenes que la tienda no subió: la tienda las muestra como «Imagen referencial».">
        <div className="space-y-4">
          <p className="text-sm font-medium text-foreground">{itemsSentence(summary)}</p>
          <ul aria-label="Dominios externos" className="divide-y divide-bd-border rounded-xl border border-bd-border">
            {summary.hosts.map((host) => (
              <li key={host.host} className="flex flex-wrap items-baseline justify-between gap-2 px-4 py-2 text-sm">
                <span className={`min-w-0 break-all text-foreground ${host.host === SITE_PATH ? "" : "font-mono"}`}>{hostLabel(host.host)}</span>
                <span className="text-xs text-muted">
                  {images(host.images)} en {plural(host.items, "ficha", "fichas")}
                </span>
              </li>
            ))}
          </ul>
          {bulk}
        </div>
      </Panel>

      <Panel id="fichas" title="Fichas por reemplazar" description="Modelos primero, después variantes y productos, por nombre.">
        <div className="space-y-4">
          <ul aria-label="Fichas con imágenes externas" className="divide-y divide-bd-border rounded-xl border border-bd-border">
            {data.results.map((row) => (
              <li key={`${row.kind}-${row.id}`} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <p className="flex flex-wrap items-center gap-2">
                    <span className={`${badge} border-bd-border text-muted`}>{KIND_LABEL[row.kind]}</span>
                    <span className="min-w-0 break-words text-sm font-medium text-foreground">{row.name}</span>
                    {!row.is_published ? (
                      <span className={`${badge} border-warning-border bg-warning-surface text-warning`}>No publicado</span>
                    ) : null}
                  </p>
                  <p className="mt-1 text-xs text-muted">
                    <span className="break-all">{row.hosts.length ? row.hosts.map(rowHostLabel).join(", ") : "(sin dominio)"}</span>
                    {" · "}
                    {images(row.image_count)}
                  </p>
                </div>
                <Link href={row.admin_url} className={`${touch} ${internalButtonClass}`} aria-label={`Editar «${row.name}»`}>
                  Editar
                </Link>
              </li>
            ))}
          </ul>
          {pages > 1 ? (
            <nav aria-label="Páginas del informe" className="flex flex-wrap items-center justify-between gap-3">
              <button type="button" className={`${touch} ${internalButtonClass}`} disabled={loading || page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}>
                Anterior
              </button>
              <span className="text-sm text-muted" aria-live="polite">Página {data.page} de {pages}</span>
              <button type="button" className={`${touch} ${internalButtonClass}`} disabled={loading || page >= pages}
                onClick={() => setPage((p) => Math.min(pages, p + 1))}>
                Siguiente
              </button>
            </nav>
          ) : null}
        </div>
      </Panel>
    </div>
  );
}
