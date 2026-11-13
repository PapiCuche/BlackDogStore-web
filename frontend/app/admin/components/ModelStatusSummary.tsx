"use client";

/**
 * CATALOG-COMMERCIAL-01 · A1b-2b — «Resumen del modelo», on top of the model's page.
 *
 * How many variants are complete, how many are drafts still waiting for «Habilitar para la venta», how many
 * need a look, how many configurations of the palette have no variant yet, and which roles the palette does
 * not cover. Read only from the palette payload the page already has (`payload.summary` and `variants`): no
 * call of its own. A number an older server does not send is left out, never guessed. «Por revisar» counts the
 * variants by the same rule as the «Por revisar» filter of «Variantes» (`reviewReasons`: draft, no price on the
 * new line, no picture, outside or short of the palette), so the two always agree.
 */

import type { AdminModelPalette } from "../lib/internal-api";
import { ROLE_LABELS, ROLE_ORDER, reviewReasons } from "../lib/palette";

function Figure({ label, value, hint, tone = "plain" }: {
  label: string; value: number; hint?: string; tone?: "plain" | "warning";
}) {
  const box = tone === "warning" && value > 0
    ? "border-warning-border bg-warning-surface"
    : "border-bd-border bg-background";
  return (
    <div className={`min-w-0 rounded-lg border px-3 py-2.5 ${box}`}>
      <dt className="text-[11px] font-semibold uppercase tracking-widest text-muted">{label}</dt>
      <dd className="mt-1 text-xl font-semibold tabular-nums text-foreground">{value}</dd>
      {hint ? <dd className="mt-0.5 text-xs text-muted">{hint}</dd> : null}
    </div>
  );
}

export function ModelStatusSummary({
  palette, error = null, onShowReview,
}: {
  /** The palette as the server last answered it; null while it loads. */
  palette: AdminModelPalette | null;
  /** Why the palette could not be read: the summary says so instead of loading forever. */
  error?: string | null;
  /** Shows the variants under «Por revisar» (the variants panel's filter). */
  onShowReview?: () => void;
}) {
  const summary = palette?.summary;
  const pendingDrafts = summary?.pending_drafts;
  const hasNew = typeof pendingDrafts === "number";
  const needsReview = (palette?.variants ?? []).filter((row) => reviewReasons(row).length > 0).length;
  const variantCount = palette?.variants.length ?? 0;
  const missing = (summary?.roles_missing ?? [])
    .filter((role) => ROLE_ORDER.includes(role))
    .sort((a, b) => ROLE_ORDER.indexOf(a) - ROLE_ORDER.indexOf(b))
    .map((role) => ROLE_LABELS[role]);

  return (
    <section
      aria-labelledby="resumen-modelo-titulo"
      className="min-w-0 rounded-xl border border-bd-border bg-surface px-5 py-4"
    >
      <h2 id="resumen-modelo-titulo" className="text-sm font-semibold text-foreground">Resumen del modelo</h2>
      {!palette && error ? (
        <p className="mt-2 text-xs text-warning">No se pudo leer el resumen: {error}</p>
      ) : !palette ? (
        <p className="mt-2 text-xs text-muted">Cargando el resumen…</p>
      ) : (
        <>
          <dl className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {hasNew ? (
              <>
                <Figure label="Completas" value={Math.max(variantCount - needsReview, 0)} hint="Variantes sin nada pendiente" />
                <Figure label="Borradores" value={pendingDrafts} hint="Esperan «Habilitar para la venta»" tone="warning" />
                <Figure label="Por revisar" value={needsReview} hint="Incluye los borradores" tone="warning" />
              </>
            ) : (
              <Figure label="Variantes" value={variantCount} />
            )}
            {palette.has_palette ? (
              <Figure label="Sin variante" value={summary?.valid_without_variant ?? 0} hint="Configuraciones de la paleta" />
            ) : null}
          </dl>
          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
            {!palette.has_palette ? (
              <p className="text-muted">
                Este modelo todavía no tiene paleta: márcala en{" "}
                <a href="#paleta" className="text-foreground underline underline-offset-4">Opciones de este modelo</a>.
              </p>
            ) : missing.length > 0 ? (
              <p className="text-muted">
                La paleta todavía no incluye: {missing.join(", ")}.{" "}
                <a href="#paleta" className="text-foreground underline underline-offset-4">Revisar la paleta</a>
              </p>
            ) : null}
            {hasNew && needsReview > 0 && onShowReview ? (
              <button
                type="button"
                onClick={onShowReview}
                className="inline-flex min-h-[44px] items-center rounded-lg border border-bd-border px-3 font-semibold text-foreground transition hover:border-foreground/25"
              >
                Ver las variantes por revisar
              </button>
            ) : null}
          </div>
        </>
      )}
    </section>
  );
}
