"use client";

/**
 * Productos › Imágenes por reemplazar › «Salud de las imágenes» — IMG-HEALTH (IMG-GATE-01).
 *
 * SÓLO LECTURA. Lo que el servidor encuentra mal en las imágenes del catálogo de la empresa: archivos rotos, enlaces
 * a otros sitios (por dominio), imágenes que la tienda todavía muestra sin licencia verificada, un mismo archivo en
 * dos colores de un modelo y variantes nuevas en venta con «Imagen pendiente». Nada se borra desde aquí: cada fila
 * lleva a la ficha donde se corrige.
 *
 * Se pide al pulsar «Revisar ahora», no al abrir la página: el servidor comprueba en el almacenamiento cada archivo.
 * «Descargar CSV» guarda lo que llegó (hasta 500 filas); el informe entero sale del comando
 * `report_image_health --company <slug>`.
 */

import Link from "next/link";
import { useState } from "react";

import {
  IMAGE_HEALTH_ISSUES, fetchImageHealth, type ImageHealthIssue, type ImageHealthReport, type ImageHealthRow,
} from "../lib/internal-api";
import { Panel, Spinner } from "./InventoryUi";
import { internalButtonClass } from "./internal-ui";

const touch = "min-h-[44px]";

export const ISSUE_LABELS: Record<ImageHealthIssue, string> = {
  broken_file: "Archivos rotos",
  external_hotlink: "Enlaces a otros sitios",
  unverified_public: "Visibles sin licencia verificada",
  shared_across_colours: "Mismo archivo en dos colores",
  variant_without_photo: "Variantes con «Imagen pendiente»",
};

const DETAIL_LABELS: Record<string, string> = {
  no_file_row: "la dirección no corresponde a ninguna subida de la empresa",
  missing_object: "el archivo ya no está en el almacenamiento",
  storage_error: "no se pudo comprobar el almacenamiento",
  external: "dirección de otro sitio",
  unknown: "origen sin registrar",
  own_photo: "foto propia sin verificar",
  supplier_authorized: "autorización de proveedor sin verificar",
  licensed_stock: "licencia de banco sin verificar",
  demo: "recurso demostrativo sin verificar",
  own_hidden: "tiene fotos propias ocultas hasta verificar su licencia",
  no_photo: "ni foto propia ni foto verificada de su acabado",
};

const COLUMNS: (keyof ImageHealthRow)[] = [
  "issue", "slot", "object_id", "object_name", "model_id", "model_name", "colour", "host", "public_id", "detail",
  "admin_url",
];

/** The rows as CSV (RFC 4180 quoting), the command's columns. */
export function healthCsv(rows: ImageHealthRow[]): string {
  const cell = (value: unknown) => {
    const text = value === null || value === undefined ? "" : String(value);
    return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return [COLUMNS.join(","), ...rows.map((row) => COLUMNS.map((column) => cell(row[column])).join(","))].join("\n") + "\n";
}

function rowText(row: ImageHealthRow): string {
  const parts = [
    row.colour ? `Colores: ${row.colour}` : "",
    row.host ? `Dominio: ${row.host}` : "",
    row.detail ? DETAIL_LABELS[row.detail] ?? row.detail : "",
  ].filter(Boolean);
  return parts.join(" · ");
}

function download(rows: ImageHealthRow[]) {
  const blob = new Blob([healthCsv(rows)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "salud-imagenes.csv";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function ImageHealthPanel({ companyId }: { companyId: number | null }) {
  const [state, setState] = useState<{ loading: boolean; data: ImageHealthReport | null; error: string | null }>(
    { loading: false, data: null, error: null });

  async function run() {
    setState((last) => ({ ...last, loading: true, error: null }));
    try {
      const data = await fetchImageHealth(companyId);
      setState({ loading: false, data, error: null });
    } catch (err) {
      setState((last) => ({
        loading: false, data: last.data,
        error: err instanceof Error && err.message ? err.message : "No se pudo revisar la salud de las imágenes.",
      }));
    }
  }

  const { data, loading, error } = state;
  return (
    <Panel id="salud" title="Salud de las imágenes"
      description="Revisión de sólo lectura: nada se borra desde aquí. Cada fila lleva a la ficha donde se corrige.">
      <div className="space-y-4" aria-busy={loading}>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className={`${touch} ${internalButtonClass}`} onClick={() => void run()} disabled={loading}>
            {data ? "Revisar de nuevo" : "Revisar ahora"}
          </button>
          {data && data.rows.length ? (
            <button type="button" className={`${touch} ${internalButtonClass}`} onClick={() => download(data.rows)}>
              Descargar CSV
            </button>
          ) : null}
        </div>
        {loading ? <Spinner label="Revisando las imágenes…" /> : null}
        {error ? <p role="alert" className="text-sm text-danger">{error}</p> : null}
        {data ? (
          <>
            <ul aria-label="Problemas de imágenes" className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
              {IMAGE_HEALTH_ISSUES.map((issue) => (
                <li key={issue} className="min-w-0 rounded-xl border border-bd-border px-3 py-2">
                  <span className="block break-words text-xs text-muted">{ISSUE_LABELS[issue]}</span>
                  <span className="block text-lg font-semibold tabular-nums text-foreground">{data.summary[issue]}</span>
                </li>
              ))}
            </ul>
            {data.summary.variants_hidden_by_gate ? (
              <p className="text-sm text-warning" data-testid="health-hidden-by-gate">
                {data.summary.variants_hidden_by_gate === 1
                  ? "1 variante tiene fotos propias que la tienda no muestra hasta verificar su licencia."
                  : `${data.summary.variants_hidden_by_gate} variantes tienen fotos propias que la tienda no muestra hasta verificar su licencia.`}
              </p>
            ) : null}
            {data.summary.external_hosts.length ? (
              <p className="break-words text-sm text-muted">
                Dominios externos: {data.summary.external_hosts.map((host) => `${host.host} (${host.images})`).join(", ")}
              </p>
            ) : null}
            {!data.summary.storage_checked ? (
              <p className="text-sm text-muted">No se comprobó el almacenamiento.</p>
            ) : null}
            {data.rows.length === 0 ? (
              <p role="status" className="text-sm text-foreground">No se encontró ningún problema en las imágenes del catálogo.</p>
            ) : (
              <ul aria-label="Detalle de problemas" className="divide-y divide-bd-border rounded-xl border border-bd-border">
                {data.rows.map((row, index) => (
                  <li key={`${row.issue}-${row.slot}-${row.object_id}-${row.public_id}-${row.host}-${index}`} className="flex flex-wrap items-center gap-3 px-4 py-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm">
                        <span className="font-semibold text-foreground">{ISSUE_LABELS[row.issue] ?? row.issue}</span>
                        {" · "}
                        <span className="break-words text-foreground">{row.object_name || `#${row.object_id}`}</span>
                      </p>
                      {rowText(row) ? <p className="mt-1 break-words text-xs text-muted">{rowText(row)}</p> : null}
                    </div>
                    <Link href={row.admin_url} className={`${touch} ${internalButtonClass}`}
                      aria-label={`Corregir «${row.object_name || row.object_id}»`}>
                      Corregir
                    </Link>
                  </li>
                ))}
              </ul>
            )}
            {data.truncated ? (
              <p className="text-sm text-muted">
                Se muestran {data.rows.length} de {data.count}. El informe completo sale del comando «report_image_health».
              </p>
            ) : null}
          </>
        ) : null}
      </div>
    </Panel>
  );
}
