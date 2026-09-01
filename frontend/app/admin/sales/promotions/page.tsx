"use client";

/**
 * Promociones — Commercial Phase C1.3.
 *
 * Two tabs, two models, and they stay apart on purpose.
 *
 *   AUTOMÁTICAS  a `Promotion`. Fires the moment a basket qualifies; nobody
 *                types anything and the cashier presses no button.
 *
 *   CÓDIGOS      a `Coupon`. Fires because somebody typed a code.
 *
 * They are administered on one screen because to a shopkeeper both are
 * "discounts I set up". They are separate models because merging them would
 * mean either every automatic promotion needs a code nobody will remember, or
 * every coupon fires unasked.
 *
 * WHAT THIS SCREEN DOES NOT SHOW is margin, profit or ROI. The platform does
 * not record what anything cost, so any such figure would be invented. It shows
 * how often each promotion fired and how much was given away — both facts.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AdminShell } from "../../components/AdminShell";
import {
  InternalControlGuard,
  type InternalContext,
} from "../../components/InternalControlGuard";
import { DashboardSection } from "../../components/dashboard-ui";
import { fetchAdminProducts, type AdminProduct } from "../../../lib/admin";
import {
  createCoupon,
  createPromotion,
  fetchCoupons,
  fetchPromotions,
  updateCoupon,
  updatePromotion,
  type CouponRow,
  type PromotionList,
  type PromotionRow,
} from "../../lib/internal-api";

function money(value: string | number) {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n)
    ? n.toLocaleString("es-PE", { style: "currency", currency: "PEN" })
    : String(value);
}

const FIELD =
  "w-full rounded-lg border border-white/[0.08] bg-black/40 px-3 py-2 text-sm text-zinc-200 outline-none transition focus:border-white/25 disabled:opacity-50";
const LABEL =
  "mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-zinc-500";

function PromotionRowView({
  promotion,
  canManage,
  onToggled,
}: {
  promotion: PromotionRow;
  canManage: boolean;
  onToggled: (next: PromotionRow) => void;
}) {
  const [busy, setBusy] = useState(false);
  const regular = promotion.items.reduce(
    (sum, i) => sum + Number(i.price) * i.quantity,
    0,
  );
  const comboPrice =
    promotion.promotion_type === "bundle_fixed_price"
      ? Number(promotion.fixed_price ?? 0)
      : regular * (1 - Number(promotion.discount_percent ?? 0) / 100);
  const saving = Math.max(regular - comboPrice, 0);

  return (
    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-zinc-200">
            {promotion.name}
            {promotion.is_live ? (
              <span className="ml-2 text-[11px] text-emerald-400/80">activa</span>
            ) : (
              <span className="ml-2 text-[11px] text-zinc-600">
                {promotion.is_active ? "fuera de fecha" : "archivada"}
              </span>
            )}
          </p>
          <p className="mt-0.5 text-[11px] text-zinc-600">
            {promotion.promotion_type_label} · prioridad {promotion.priority} ·{" "}
            {promotion.branch_scope === "all"
              ? "todas las sucursales"
              : promotion.branches.length
                ? promotion.branches.map((b) => b.name).join(", ")
                : "ninguna sucursal seleccionada"}
          </p>
        </div>
      </div>

      <div className="mt-3 space-y-1 text-xs text-zinc-500">
        {promotion.items.map((item) => (
          <p key={item.product}>
            {item.quantity}× {item.product_name}{" "}
            <span className="text-zinc-700">{money(item.price)}</span>
          </p>
        ))}
      </div>

      <div className="mt-3 flex flex-wrap items-baseline justify-between gap-3 border-t border-white/[0.06] pt-3 text-sm">
        <span className="text-zinc-600 line-through">{money(regular)}</span>
        <span className="text-white">{money(comboPrice)}</span>
        <span className="text-emerald-400/80">ahorro {money(saving)}</span>
      </div>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-3 text-[11px] text-zinc-600">
        <span>
          Aplicada {promotion.stats.applications ?? 0} vez(ces) en{" "}
          {promotion.stats.orders ?? 0} venta(s) · descontado{" "}
          {money(promotion.stats.discount_given ?? "0")}
        </span>
        {canManage ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              void (async () => {
                try {
                  onToggled(
                    await updatePromotion(promotion.id, null, {
                      is_active: !promotion.is_active,
                    }),
                  );
                } finally {
                  setBusy(false);
                }
              })();
            }}
            className="text-zinc-500 underline underline-offset-2 transition hover:text-zinc-300"
          >
            {promotion.is_active ? "Archivar" : "Reactivar"}
          </button>
        ) : null}
      </div>
    </div>
  );
}

function ComboForm({
  companyId,
  onCreated,
  onCancel,
}: {
  companyId: number | null;
  onCreated: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [type, setType] = useState<"bundle_fixed_price" | "bundle_percent">(
    "bundle_fixed_price",
  );
  const [fixedPrice, setFixedPrice] = useState("");
  const [percent, setPercent] = useState("10");
  const [priority, setPriority] = useState("0");
  const [picked, setPicked] = useState<{ product: number; name: string; price: string; quantity: number }[]>([]);
  const [term, setTerm] = useState("");
  const [hits, setHits] = useState<AdminProduct[]>([]);
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      if (term.trim().length < 2) {
        setHits([]);
        return;
      }
      void (async () => {
        try {
          const page = await fetchAdminProducts({ search: term.trim(), page_size: 8 });
          if (!cancelled) setHits(page.results);
        } catch {
          if (!cancelled) setHits([]);
        }
      })();
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [term]);

  const regular = picked.reduce((sum, p) => sum + Number(p.price) * p.quantity, 0);
  const combo =
    type === "bundle_fixed_price"
      ? Number(fixedPrice || 0)
      : regular * (1 - Number(percent || 0) / 100);
  const saving_ = Math.max(regular - combo, 0);

  async function save() {
    setSaving(true);
    setErrors({});
    setError(null);
    try {
      await createPromotion(companyId, {
        name,
        promotion_type: type,
        fixed_price: type === "bundle_fixed_price" ? fixedPrice : null,
        discount_percent: type === "bundle_percent" ? percent : null,
        priority: Number(priority) || 0,
        is_active: true,
        items: picked.map((p) => ({ product: p.product, quantity: p.quantity })),
      });
      onCreated();
    } catch (err) {
      const fields = (err as { fields?: Record<string, string> }).fields;
      if (fields) setErrors(fields);
      else setError(err instanceof Error ? err.message : "No se pudo crear.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4 rounded-xl border border-white/[0.06] bg-white/[0.02] p-5">
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className={LABEL} htmlFor="combo-name">
            Nombre
          </label>
          <input
            id="combo-name"
            className={`${FIELD} ${errors.name ? "border-red-500/50" : ""}`}
            value={name}
            maxLength={150}
            onChange={(e) => setName(e.target.value)}
          />
          {errors.name ? <p className="mt-1 text-xs text-red-400">{errors.name}</p> : null}
        </div>
        <div>
          <label className={LABEL} htmlFor="combo-type">
            Beneficio
          </label>
          <div className="flex gap-2">
            <select
              id="combo-type"
              className={`${FIELD} flex-1`}
              value={type}
              onChange={(e) =>
                setType(e.target.value as "bundle_fixed_price" | "bundle_percent")
              }
            >
              <option value="bundle_fixed_price">Precio del combo</option>
              <option value="bundle_percent">Porcentaje</option>
            </select>
            <input
              type="number"
              min={0}
              className={`${FIELD} w-28`}
              value={type === "bundle_fixed_price" ? fixedPrice : percent}
              onChange={(e) =>
                type === "bundle_fixed_price"
                  ? setFixedPrice(e.target.value)
                  : setPercent(e.target.value)
              }
            />
          </div>
        </div>
      </div>

      <div>
        <label className={LABEL} htmlFor="combo-search">
          Productos del combo
        </label>
        <input
          id="combo-search"
          className={FIELD}
          placeholder="Buscar producto para añadir"
          value={term}
          onChange={(e) => setTerm(e.target.value)}
        />
        {hits.length ? (
          <div className="mt-2 overflow-hidden rounded-lg border border-white/[0.06]">
            {hits.map((h) => (
              <button
                key={h.id}
                type="button"
                onClick={() => {
                  setPicked((prev) =>
                    prev.some((p) => p.product === h.id)
                      ? prev
                      : [
                          ...prev,
                          { product: h.id, name: h.name, price: String(h.price), quantity: 1 },
                        ],
                  );
                  setTerm("");
                }}
                className="flex w-full justify-between border-b border-white/[0.04] px-3 py-2 text-left text-sm transition last:border-0 hover:bg-white/[0.03]"
              >
                <span className="text-zinc-300">{h.name}</span>
                <span className="font-mono text-xs text-zinc-500">{money(h.price)}</span>
              </button>
            ))}
          </div>
        ) : null}
      </div>

      {picked.length ? (
        <div className="space-y-2">
          {picked.map((p) => (
            <div key={p.product} className="flex items-center gap-3 rounded-lg bg-black/30 p-2">
              <span className="flex-1 text-sm text-zinc-200">{p.name}</span>
              <input
                type="number"
                min={1}
                value={p.quantity}
                onChange={(e) =>
                  setPicked((prev) =>
                    prev.map((x) =>
                      x.product === p.product
                        ? { ...x, quantity: Math.max(1, Number(e.target.value)) }
                        : x,
                    ),
                  )
                }
                className="w-16 rounded border border-white/[0.08] bg-black/40 px-2 py-1 text-sm text-zinc-200 outline-none"
              />
              <span className="w-24 text-right font-mono text-xs text-zinc-500">
                {money(Number(p.price) * p.quantity)}
              </span>
              <button
                type="button"
                onClick={() => setPicked((prev) => prev.filter((x) => x.product !== p.product))}
                className="text-xs text-zinc-600 transition hover:text-red-400"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      ) : null}
      {errors.items ? <p className="text-xs text-red-400">{errors.items}</p> : null}

      {picked.length >= 2 ? (
        <div className="flex flex-wrap items-baseline justify-between gap-3 border-t border-white/[0.06] pt-3 text-sm">
          <span className="text-zinc-600 line-through">{money(regular)}</span>
          <span className="text-white">{money(combo)}</span>
          <span className="text-emerald-400/80">ahorro {money(saving_)}</span>
        </div>
      ) : (
        <p className="text-[11px] text-zinc-600">
          Un combo necesita al menos dos productos.
        </p>
      )}

      <div className="flex items-center gap-3">
        <div className="w-28">
          <label className={LABEL} htmlFor="combo-priority">
            Prioridad
          </label>
          <input
            id="combo-priority"
            type="number"
            className={FIELD}
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
          />
        </div>
        <div className="flex flex-1 items-end gap-3 pt-5">
          <button
            type="button"
            disabled={saving || picked.length < 2 || !name.trim()}
            onClick={() => void save()}
            className="rounded-lg border border-white/15 px-4 py-2 text-sm text-zinc-200 transition hover:border-white/30 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {saving ? "Creando…" : "Crear promoción"}
          </button>
          <button
            type="button"
            onClick={onCancel}
            className="text-sm text-zinc-500 transition hover:text-zinc-300"
          >
            Cancelar
          </button>
        </div>
      </div>
      {error ? <p className="text-sm text-red-400">{error}</p> : null}
    </div>
  );
}


function PromotionsContent({ ctx }: { ctx: InternalContext }) {
  const companyId = ctx.selectedCompanyId;

  const [tab, setTab] = useState<"auto" | "codes">("auto");
  const [data, setData] = useState<PromotionList | null>(null);
  const [coupons, setCoupons] = useState<{ can_manage: boolean; results: CouponRow[] } | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [creating, setCreating] = useState(false);
  const [newCode, setNewCode] = useState("");
  const [newPercent, setNewPercent] = useState("10");
  const [couponError, setCouponError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setData(await fetchPromotions(companyId));
    setCoupons(await fetchCoupons(companyId));
  }, [companyId]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setLoading(true);
      try {
        await load();
        if (!cancelled) setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "No se pudo cargar.");
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
        <p className="py-8 text-sm text-zinc-600">Cargando promociones…</p>
      </AdminShell>
    );
  }
  if (error || !data) {
    return (
      <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
        <div className="rounded-xl border border-red-500/20 bg-red-500/5 px-5 py-4 text-sm text-red-400">
          {error ?? "Sin datos."}
        </div>
      </AdminShell>
    );
  }

  return (
    <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
      <div className="space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex gap-1 rounded-lg border border-white/[0.08] p-1">
            {(
              [
                ["auto", "Automáticas y combos"],
                ["codes", "Códigos"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => setTab(value)}
                className={`rounded px-3 py-1 text-xs transition ${
                  tab === value ? "bg-white/10 text-white" : "text-zinc-500 hover:text-zinc-300"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <Link
            href="/admin/sales"
            className="text-sm text-zinc-500 transition hover:text-zinc-300"
          >
            ← Resumen comercial
          </Link>
        </div>

        {tab === "auto" ? (
          <DashboardSection
            title="Promociones automáticas"
            description="Se aplican solas cuando el carrito califica. Nadie escribe un código."
            action={
              data.can_manage && !creating ? (
                <button
                  type="button"
                  onClick={() => setCreating(true)}
                  className="rounded-lg border border-white/15 px-3 py-1.5 text-sm text-zinc-200 transition hover:border-white/30"
                >
                  Nuevo combo
                </button>
              ) : null
            }
          >
            {creating ? (
              <ComboForm
                companyId={companyId}
                onCancel={() => setCreating(false)}
                onCreated={() => {
                  setCreating(false);
                  void load();
                }}
              />
            ) : null}
            {data.results.length === 0 && !creating ? (
              <p className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-5 py-8 text-center text-sm text-zinc-500">
                Todavía no hay promociones configuradas.
              </p>
            ) : (
              <div className="grid gap-4 lg:grid-cols-2">
                {data.results.map((p) => (
                  <PromotionRowView
                    key={p.id}
                    promotion={p}
                    canManage={data.can_manage}
                    onToggled={(next) =>
                      setData((prev) =>
                        prev
                          ? {
                              ...prev,
                              results: prev.results.map((r) => (r.id === next.id ? next : r)),
                            }
                          : prev,
                      )
                    }
                  />
                ))}
              </div>
            )}
            <p className="text-[11px] text-zinc-600">
              Cuando dos promociones quieren la misma unidad, gana la de mayor prioridad y
              la otra no encuentra unidades libres. El resultado es siempre el mismo para
              el mismo carrito.
            </p>
          </DashboardSection>
        ) : (
          <DashboardSection
            title="Códigos de descuento"
            description="Se aplican cuando alguien escribe el código en la caja o en el checkout."
          >
            {coupons?.can_manage ? (
              <div className="flex flex-wrap items-end gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                <div className="flex-1">
                  <label className={LABEL} htmlFor="new-code">
                    Código
                  </label>
                  <input
                    id="new-code"
                    className={`${FIELD} font-mono`}
                    value={newCode}
                    onChange={(e) => setNewCode(e.target.value)}
                  />
                </div>
                <div className="w-28">
                  <label className={LABEL} htmlFor="new-percent">
                    Descuento %
                  </label>
                  <input
                    id="new-percent"
                    type="number"
                    min={1}
                    max={100}
                    className={FIELD}
                    value={newPercent}
                    onChange={(e) => setNewPercent(e.target.value)}
                  />
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setCouponError(null);
                    void (async () => {
                      try {
                        await createCoupon(companyId, {
                          code: newCode,
                          discount_percent: Number(newPercent),
                        });
                        setNewCode("");
                        await load();
                      } catch (err) {
                        setCouponError(
                          err instanceof Error ? err.message : "No se pudo crear.",
                        );
                      }
                    })();
                  }}
                  className="rounded-lg border border-white/15 px-4 py-2 text-sm text-zinc-200 transition hover:border-white/30"
                >
                  Crear código
                </button>
              </div>
            ) : null}
            {couponError ? <p className="text-sm text-red-400">{couponError}</p> : null}

            {!coupons?.results.length ? (
              <p className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-5 py-8 text-center text-sm text-zinc-500">
                No hay códigos configurados.
              </p>
            ) : (
              <div className="overflow-x-auto rounded-xl border border-white/[0.06]">
                <table className="w-full min-w-[32rem] text-left text-sm">
                  <thead className="border-b border-white/[0.06] text-[11px] uppercase tracking-widest text-zinc-500">
                    <tr>
                      <th className="px-4 py-3 font-semibold">Código</th>
                      <th className="px-4 py-3 text-right font-semibold">Descuento</th>
                      <th className="px-4 py-3 font-semibold">Estado</th>
                      <th className="px-4 py-3" />
                    </tr>
                  </thead>
                  <tbody>
                    {coupons.results.map((c) => (
                      <tr key={c.id} className="border-b border-white/[0.04] last:border-0">
                        <td className="px-4 py-3 font-mono text-zinc-200">{c.code}</td>
                        <td className="px-4 py-3 text-right text-zinc-400">
                          {c.discount_percent}%
                        </td>
                        <td className="px-4 py-3 text-xs">
                          {c.is_expired ? (
                            <span className="text-zinc-600">vencido</span>
                          ) : c.is_active ? (
                            <span className="text-emerald-400/80">activo</span>
                          ) : (
                            <span className="text-zinc-600">inactivo</span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-right">
                          {coupons.can_manage ? (
                            <button
                              type="button"
                              onClick={() => {
                                void (async () => {
                                  await updateCoupon(c.id, companyId, {
                                    is_active: !c.is_active,
                                  });
                                  await load();
                                })();
                              }}
                              className="text-xs text-zinc-500 underline underline-offset-2 transition hover:text-zinc-300"
                            >
                              {c.is_active ? "Desactivar" : "Activar"}
                            </button>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </DashboardSection>
        )}
      </div>
    </AdminShell>
  );
}

export default function PromotionsPage() {
  return (
    <InternalControlGuard>{(ctx) => <PromotionsContent ctx={ctx} />}</InternalControlGuard>
  );
}
