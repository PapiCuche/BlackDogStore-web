"use client";

/**
 * Productos › Modelos › «Preparación del catálogo» — CATALOG-READINESS-01.
 *
 * What the company still lacks before it can register its whole inventory (new devices, used devices and
 * accessories) with the supplier-list assistant and «Recibir mercadería». A GUIDE: it only reads
 * `GET /admin/catalog/readiness/` (`products.view`) and links each check to the screen that fixes it.
 * Nothing here writes. The rules live in `backend/store/catalog_readiness.py`.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  CatalogApiError,
  fetchCatalogReadiness,
  type CatalogReadiness,
  type CatalogReadinessCheck,
  type CatalogReadinessKind,
  type CatalogReadinessStatus,
} from "../lib/internal-api";
import { EmptyBox, Panel, Spinner } from "./InventoryUi";
import { internalButtonClass } from "./internal-ui";

const FALLBACK_ERROR = "No se pudo revisar la preparación del catálogo.";

export const READINESS_STATUS_LABELS: Record<CatalogReadinessStatus, string> = {
  missing: "Falta",
  warning: "Revisar",
  ok: "Listo",
};

const KIND_LABELS: Partial<Record<CatalogReadinessKind, string>> = {
  recommended: "Recomendado",
  optional: "Opcional",
  info: "Informativo",
};

const STATUS_TONE: Record<CatalogReadinessStatus, string> = {
  missing: "border-danger-border bg-danger-surface text-danger",
  warning: "border-warning-border bg-warning-surface text-warning",
  ok: "border-success-border bg-success-surface text-success",
};

const STATUS_ICON: Record<CatalogReadinessStatus, string> = { missing: "✕", warning: "!", ok: "✓" };

const STATUS_ORDER: Record<CatalogReadinessStatus, number> = { missing: 0, warning: 1, ok: 2 };

function StatusBadge({ status }: { status: CatalogReadinessStatus }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full border px-2.5 py-1 text-[11px] font-semibold ${STATUS_TONE[status]}`}
    >
      <span aria-hidden="true">{STATUS_ICON[status]}</span>
      <span>{READINESS_STATUS_LABELS[status]}</span>
    </span>
  );
}

function CheckItem({ check }: { check: CatalogReadinessCheck }) {
  const kind = KIND_LABELS[check.kind];
  return (
    <li className="rounded-lg border border-bd-border bg-background p-4" aria-label={check.title}>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={check.status} />
            <h3 className="text-sm font-semibold text-foreground">{check.title}</h3>
            {kind ? <span className="text-xs text-muted">{kind}</span> : null}
          </div>
          <p className="text-sm text-muted [overflow-wrap:anywhere]">{check.detail}</p>
          {check.samples.length > 0 ? (
            <ul className="flex flex-wrap gap-x-3 gap-y-1 pt-1 text-xs" aria-label={`Modelos de «${check.title}»`}>
              {check.samples.map((sample) => (
                <li key={sample.id}>
                  <Link href={sample.path} className="text-foreground underline underline-offset-2 hover:text-muted">
                    {sample.name}
                  </Link>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        <Link
          href={check.action}
          className={`${internalButtonClass} inline-flex shrink-0 items-center justify-center self-start text-center`}
        >
          {check.action_label}
        </Link>
      </div>
    </li>
  );
}

function summaryLine({ summary }: CatalogReadiness) {
  return `${summary.missing} por completar · ${summary.warning} por revisar · ${summary.ok} listas`;
}

export function CatalogReadinessPanel({ companyId }: { companyId: number | null }) {
  const [data, setData] = useState<CatalogReadiness | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Only the answer sets state (no synchronous setState in the effect); a late answer for an
  // earlier company is dropped.
  const request = useCallback(() => {
    let alive = true;
    fetchCatalogReadiness(companyId)
      .then((body) => {
        if (!alive) return;
        setData(body);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!alive) return;
        setData(null);
        setError(err instanceof CatalogApiError && err.message ? err.message : FALLBACK_ERROR);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [companyId]);

  useEffect(() => request(), [request]);

  function retry() {
    setError(null);
    setLoading(true);
    request();
  }

  const checks = data?.checks ?? [];
  const pending = checks
    .filter((check) => check.status !== "ok")
    .sort((a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status]);
  const done = checks.filter((check) => check.status === "ok");

  return (
    <Panel
      id="preparacion"
      title="Preparación del catálogo"
      description="Lo que falta antes de registrar todo tu inventario —equipos nuevos, seminuevos y accesorios— con el asistente de listas de proveedor y «Recibir mercadería». Sólo revisa: cada punto te lleva a la pantalla donde se corrige."
    >
      {loading && !data ? <Spinner label="Revisando el catálogo…" /> : null}
      {error ? (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-danger-border bg-danger-surface px-4 py-3">
          <p className="text-sm text-danger">{error}</p>
          <button type="button" className={internalButtonClass} onClick={retry} disabled={loading}>
            Reintentar
          </button>
        </div>
      ) : null}
      {data && checks.length === 0 ? <EmptyBox message="No hay comprobaciones para mostrar." /> : null}
      {data && checks.length > 0 ? (
        <div className="space-y-4">
          {data.ready ? (
            <p role="status" className="rounded-lg border border-success-border bg-success-surface px-4 py-3 text-sm font-medium text-success">
              {pending.length === 0
                ? "Todo listo: ya puedes registrar tu inventario con el asistente de proveedores y «Recibir mercadería»."
                : "Lo indispensable está listo. Quedan recomendaciones por revisar."}
            </p>
          ) : (
            <p className="text-sm text-muted">{summaryLine(data)}</p>
          )}
          {pending.length > 0 ? (
            <ul className="space-y-3" aria-label="Pendientes">
              {pending.map((check) => <CheckItem key={check.key} check={check} />)}
            </ul>
          ) : null}
          {done.length > 0 ? (
            <details className="group rounded-lg border border-bd-border">
              <summary className="cursor-pointer select-none px-4 py-3 text-sm font-semibold text-foreground">
                {`Ya está listo (${done.length})`}
              </summary>
              <ul className="space-y-3 px-4 pb-4" aria-label="Listo">
                {done.map((check) => <CheckItem key={check.key} check={check} />)}
              </ul>
            </details>
          ) : null}
        </div>
      ) : null}
    </Panel>
  );
}
