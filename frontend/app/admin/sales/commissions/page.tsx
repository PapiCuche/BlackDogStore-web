"use client";

/**
 * Comisiones — Commercial Phase C1.2.
 *
 * Two things live here, and they are deliberately different in kind.
 *
 *   WHAT HAS BEEN EARNED comes from the commission ledger: one frozen row per
 *   sale, at the rate agreed when it happened. It is history and it does not
 *   move.
 *
 *   WHAT THE RATE IS TODAY is configuration. Changing it affects the next sale
 *   and nothing before it.
 *
 * The screen shows them side by side and never multiplies one by the other. A
 * seller moved from 3% to 5% is owed 3% on last month's sales; a dashboard that
 * recomputed the total from today's rate would quietly restate a debt the
 * company already incurred.
 *
 * Restricted on purpose: `sales.commissions.view` to read, and additionally
 * `sales.commissions.manage` to change a rate. Neither is granted to the sales
 * preset — what a colleague earns is not part of working a till.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "../../components/AdminShell";
import {
  InternalControlGuard,
  type InternalContext,
} from "../../components/InternalControlGuard";
import { DashboardSection } from "../../components/dashboard-ui";
import {
  fetchCommissionSettings,
  fetchCommissions,
  updateCommissionRate,
  type CommissionReport,
  type CommissionSetting,
} from "../../lib/internal-api";

function money(value: string | number) {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n)
    ? n.toLocaleString("es-PE", { style: "currency", currency: "PEN" })
    : String(value);
}

const WINDOWS: [number, string][] = [
  [1, "Hoy"],
  [7, "7 días"],
  [30, "30 días"],
  [90, "90 días"],
];

function RateEditor({
  row,
  companyId,
  onSaved,
}: {
  row: CommissionSetting;
  companyId: number | null;
  onSaved: (rate: string) => void;
}) {
  const [value, setValue] = useState(row.commission_rate_percent);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = value !== row.commission_rate_percent;

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const saved = await updateCommissionRate(row.membership_id, companyId, value);
      onSaved(saved.commission_rate_percent);
    } catch (err) {
      const fields = (err as { fields?: Record<string, string> }).fields;
      setError(
        fields?.commission_rate_percent ??
          (err instanceof Error ? err.message : "No se pudo guardar."),
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex items-center justify-end gap-2">
      <input
        type="number"
        min={0}
        max={100}
        step="0.01"
        value={value}
        disabled={saving}
        onChange={(e) => setValue(e.target.value)}
        className="w-24 rounded border border-bd-border bg-background/40 px-2 py-1 text-right text-sm text-foreground outline-none"
      />
      <span className="text-xs text-muted">%</span>
      {dirty ? (
        <button
          type="button"
          disabled={saving}
          onClick={() => void save()}
          className="rounded border border-bd-border px-2.5 py-1 text-xs text-foreground transition hover:border-bd-border disabled:opacity-40"
        >
          {saving ? "…" : "Guardar"}
        </button>
      ) : null}
      {error ? <span className="text-xs text-danger">{error}</span> : null}
    </div>
  );
}

function CommissionsContent({ ctx }: { ctx: InternalContext }) {
  const companyId = ctx.selectedCompanyId;

  const [report, setReport] = useState<CommissionReport | null>(null);
  const [settings, setSettings] = useState<{
    can_manage: boolean;
    results: CommissionSetting[];
  } | null>(null);
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setReport(await fetchCommissions(companyId, { days }));
    try {
      setSettings(await fetchCommissionSettings(companyId));
    } catch {
      // Configuring rates needs the manage capability on top. Not having it
      // hides that table; it must not cost the earnings view.
      setSettings(null);
    }
  }, [companyId, days]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setLoading(true);
      try {
        await load();
        if (!cancelled) setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "No se pudieron cargar las comisiones.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  if (loading) {
    return (
      <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
        <p className="py-8 text-sm text-muted">Cargando comisiones…</p>
      </AdminShell>
    );
  }
  if (error || !report) {
    return (
      <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
        <div className="rounded-xl border border-danger-border bg-danger-surface px-5 py-4 text-sm text-danger">
          {error ?? "Sin datos."}
        </div>
      </AdminShell>
    );
  }

  return (
    <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
      <div className="space-y-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex gap-1 rounded-lg border border-bd-border p-1">
            {WINDOWS.map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => setDays(value)}
                className={`rounded px-2.5 py-1 text-xs transition ${
                  days === value ? "bg-surface-2 text-foreground" : "text-muted hover:text-foreground/85"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <Link
            href="/admin/sales"
            className="text-sm text-muted transition hover:text-foreground/85"
          >
            ← Resumen comercial
          </Link>
        </div>

        <DashboardSection
          title="Comisiones devengadas"
          description={report.note}
          action={
            <span className="font-display text-lg text-foreground">
              {money(report.total_commission)}
            </span>
          }
        >
          {report.results.length === 0 ? (
            <p className="rounded-xl border border-bd-border bg-surface px-5 py-8 text-center text-sm text-muted">
              No hay comisiones devengadas en este periodo.
            </p>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-bd-border">
              <table className="w-full min-w-[40rem] text-left text-sm">
                <thead className="border-b border-bd-border text-[11px] uppercase tracking-widest text-muted">
                  <tr>
                    <th className="px-4 py-3 font-semibold">Vendedor</th>
                    <th className="px-4 py-3 text-right font-semibold">Ventas</th>
                    <th className="px-4 py-3 text-right font-semibold">Venta neta</th>
                    <th className="px-4 py-3 text-right font-semibold">Tasa actual</th>
                    <th className="px-4 py-3 text-right font-semibold">Comisión</th>
                  </tr>
                </thead>
                <tbody>
                  {report.results.map((r) => (
                    <tr
                      key={`${r.seller_id}-${r.seller_name}`}
                      className="border-b border-bd-border last:border-0"
                    >
                      <td className="px-4 py-3 text-foreground">{r.seller_name}</td>
                      <td className="px-4 py-3 text-right font-mono text-muted">
                        {r.sales}
                      </td>
                      <td className="px-4 py-3 text-right font-mono text-muted">
                        {money(r.net_amount)}
                      </td>
                      <td className="px-4 py-3 text-right text-xs text-muted">
                        {r.current_rate_percent ? `${r.current_rate_percent}%` : "—"}
                      </td>
                      <td className="px-4 py-3 text-right font-mono text-foreground">
                        {money(r.commission)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </DashboardSection>

        {settings ? (
          <DashboardSection
            title="Porcentajes del equipo"
            description={
              settings.can_manage
                ? "Cambiar una tasa afecta a las ventas futuras. Lo ya devengado no se recalcula."
                : "Sólo lectura: no tienes permiso para configurar comisiones."
            }
          >
            <div className="overflow-x-auto rounded-xl border border-bd-border">
              <table className="w-full min-w-[32rem] text-left text-sm">
                <thead className="border-b border-bd-border text-[11px] uppercase tracking-widest text-muted">
                  <tr>
                    <th className="px-4 py-3 font-semibold">Miembro</th>
                    <th className="px-4 py-3 font-semibold">Rol</th>
                    <th className="px-4 py-3 text-right font-semibold">Comisión</th>
                  </tr>
                </thead>
                <tbody>
                  {settings.results.map((row) => (
                    <tr
                      key={row.membership_id}
                      className="border-b border-bd-border last:border-0"
                    >
                      <td className="px-4 py-3 text-foreground">{row.name}</td>
                      <td className="px-4 py-3 text-xs text-muted">{row.role}</td>
                      <td className="px-4 py-3">
                        {settings.can_manage ? (
                          <RateEditor
                            row={row}
                            companyId={companyId}
                            onSaved={(rate) =>
                              setSettings((prev) =>
                                prev
                                  ? {
                                      ...prev,
                                      results: prev.results.map((r) =>
                                        r.membership_id === row.membership_id
                                          ? { ...r, commission_rate_percent: rate }
                                          : r,
                                      ),
                                    }
                                  : prev,
                              )
                            }
                          />
                        ) : (
                          <p className="text-right font-mono text-muted">
                            {row.commission_rate_percent}%
                          </p>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </DashboardSection>
        ) : null}
      </div>
    </AdminShell>
  );
}

export default function CommissionsPage() {
  return (
    <InternalControlGuard>{(ctx) => <CommissionsContent ctx={ctx} />}</InternalControlGuard>
  );
}
