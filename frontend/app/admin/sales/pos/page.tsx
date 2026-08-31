"use client";

/**
 * Punto de venta — Commercial Phase C1.
 *
 * Designed around one fact: the person using this is standing at a counter with
 * a customer waiting, holding a scanner in one hand. Everything else follows.
 *
 *   THE SCAN FIELD OWNS THE FOCUS, but not rudely. A keyboard-wedge scanner
 *   types a code and presses Enter, so the caret must already be in the right
 *   box — otherwise the code lands in whatever field was last touched. Focus is
 *   returned after every scan and after a sale, and NOT while somebody is
 *   deliberately typing in the search box, the quantity, or the customer field.
 *
 *   SCANNING THE SAME ARTICLE TWICE ADDS TWO, rather than complaining about a
 *   duplicate. Two identical cables is the ordinary case.
 *
 *   THE BASKET SURVIVES A FAILURE. If the shelf is empty or the network drops,
 *   the lines stay on screen. Clearing them would mean re-scanning a full
 *   basket in front of a queue.
 *
 *   THE BROWSER NEVER DECIDES A PRICE. It shows one so the operator can read a
 *   total aloud; the server charges from its own catalogue.
 *
 *   ONE IDEMPOTENCY KEY PER BASKET. Minted when the basket becomes non-empty,
 *   retired only after a confirmed sale. A double click, a timeout, or a
 *   retried request therefore carries the SAME key, and the backend answers
 *   with the sale it already made instead of making a second one.
 */

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { AdminShell } from "../../components/AdminShell";
import {
  InternalControlGuard,
  type InternalContext,
} from "../../components/InternalControlGuard";
import {
  PosConflictError,
  PosStockError,
  fetchPosContext,
  posLookup,
  posSale,
  posSearch,
  type PosContext,
  type PosProduct,
  type PosSaleResult,
} from "../../lib/internal-api";

type Line = {
  product: number;
  name: string;
  price: string;
  quantity: number;
  available: number;
};

type Feedback = { kind: "ok" | "warn" | "error"; text: string } | null;

function money(value: string | number) {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n)
    ? n.toLocaleString("es-PE", { style: "currency", currency: "PEN" })
    : String(value);
}

function newKey() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `pos-${Date.now()}-${Math.round(Math.random() * 1e9)}`;
}

const FIELD =
  "w-full rounded-lg border border-white/[0.08] bg-black/40 px-3 py-2 text-sm text-zinc-200 outline-none transition focus:border-white/25 disabled:opacity-50";

function PosContent({ ctx }: { ctx: InternalContext }) {
  const companyId = ctx.selectedCompanyId;

  const [context, setContext] = useState<PosContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [fatal, setFatal] = useState<string | null>(null);

  const [branch, setBranch] = useState<number | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [payment, setPayment] = useState("cash");
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [charging, setCharging] = useState(false);
  const [done, setDone] = useState<PosSaleResult | null>(null);

  const [scan, setScan] = useState("");
  const [term, setTerm] = useState("");
  const [found, setFound] = useState<PosProduct[]>([]);

  const scanRef = useRef<HTMLInputElement | null>(null);
  // Held across renders and across retries: the same basket keeps one key.
  const keyRef = useRef<string>(newKey());

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setLoading(true);
      try {
        const c = await fetchPosContext(companyId);
        if (cancelled) return;
        setContext(c);
        setBranch(c.default_branch);
        setPayment(c.payment_methods[0]?.value ?? "cash");
        setFatal(null);
      } catch (err) {
        if (!cancelled) {
          setFatal(err instanceof Error ? err.message : "No se pudo abrir la caja.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [companyId]);

  const focusScan = useCallback(() => {
    // Only when nothing else is deliberately focused — stealing the caret out
    // of the quantity box while somebody edits it is worse than losing a scan.
    const active = document.activeElement;
    if (active && active !== document.body && active !== scanRef.current) return;
    scanRef.current?.focus();
  }, []);

  const addProduct = useCallback((p: PosProduct) => {
    setLines((prev) => {
      const existing = prev.find((l) => l.product === p.id);
      if (existing) {
        return prev.map((l) =>
          l.product === p.id ? { ...l, quantity: l.quantity + 1, available: p.available } : l,
        );
      }
      return [
        ...prev,
        { product: p.id, name: p.name, price: p.price, quantity: 1, available: p.available },
      ];
    });
    setFeedback({ kind: "ok", text: `${p.name} · ${money(p.price)}` });
  }, []);

  async function handleScan(code: string) {
    const clean = code.trim();
    if (!clean || branch === null) return;
    setScan("");
    try {
      const product = await posLookup(companyId, branch, clean);
      if (product === null) {
        setFeedback({ kind: "error", text: `Código no encontrado: ${clean}` });
        return;
      }
      if (product.available <= 0) {
        setFeedback({
          kind: "warn",
          text: `${product.name} sin stock en esta sucursal.`,
        });
        return;
      }
      addProduct(product);
    } catch (err) {
      setFeedback({
        kind: "error",
        text: err instanceof Error ? err.message : "Error al leer el código.",
      });
    } finally {
      focusScan();
    }
  }

  useEffect(() => {
    let cancelled = false;
    // Clearing happens inside the timer rather than in the effect body: setting
    // state synchronously while the effect runs schedules a second render
    // before the first has committed.
    const timer = setTimeout(() => {
      if (branch === null || term.trim().length < 2) {
        setFound([]);
        return;
      }
      void (async () => {
        try {
          const results = await posSearch(companyId, branch, term.trim());
          if (!cancelled) setFound(results);
        } catch {
          if (!cancelled) setFound([]);
        }
      })();
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [term, branch, companyId]);

  const total = lines.reduce((sum, l) => sum + Number(l.price) * l.quantity, 0);
  const units = lines.reduce((sum, l) => sum + l.quantity, 0);

  function setQuantity(productId: number, quantity: number) {
    setLines((prev) =>
      quantity <= 0
        ? prev.filter((l) => l.product !== productId)
        : prev.map((l) => (l.product === productId ? { ...l, quantity } : l)),
    );
  }

  async function charge() {
    if (charging || !lines.length || branch === null) return;
    setCharging(true);
    setFeedback(null);
    try {
      const result = await posSale(companyId, {
        branch,
        items: lines.map((l) => ({ product: l.product, quantity: l.quantity })),
        payment_method: payment,
        idempotency_key: keyRef.current,
      });
      setDone(result);
      setLines([]);
      // A new basket gets a new key. Retiring it only HERE is what makes a
      // retry of the request above idempotent rather than a second sale.
      keyRef.current = newKey();
    } catch (err) {
      if (err instanceof PosStockError) {
        const extra = err.elsewhere.length
          ? ` Disponible en: ${err.elsewhere
              .map((e) => `${e.branch} (${e.quantity})`)
              .join(", ")}.`
          : "";
        setFeedback({ kind: "error", text: err.message + extra });
      } else if (err instanceof PosConflictError) {
        setFeedback({
          kind: "error",
          text: `${err.message} Pedido existente: #${err.existingOrder ?? "?"}.`,
        });
      } else {
        setFeedback({
          kind: "error",
          text: err instanceof Error ? err.message : "No se pudo cobrar.",
        });
      }
      // The basket is intentionally left intact.
    } finally {
      setCharging(false);
      focusScan();
    }
  }

  if (loading) {
    return (
      <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
        <p className="py-8 text-sm text-zinc-600">Abriendo caja…</p>
      </AdminShell>
    );
  }
  if (fatal || !context) {
    return (
      <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
        <div className="rounded-xl border border-red-500/20 bg-red-500/5 px-5 py-4 text-sm text-red-400">
          {fatal ?? "No se pudo abrir el punto de venta."}
        </div>
      </AdminShell>
    );
  }

  if (done) {
    return (
      <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
        <div className="mx-auto max-w-lg space-y-5 py-10 text-center">
          <p className="text-sm uppercase tracking-widest text-emerald-400/80">
            Venta registrada
          </p>
          <p className="font-display text-3xl text-white">{money(done.total)}</p>
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-5 text-left text-sm text-zinc-400">
            <p>Pedido #{done.order_id}</p>
            <p>Sucursal: {done.branch.name}</p>
            <p>Vendedor: {done.seller || "—"}</p>
            <p>
              Medio de pago:{" "}
              {context.payment_methods.find((m) => m.value === done.payment_method)?.label ??
                done.payment_method}
            </p>
          </div>
          <div className="flex flex-wrap justify-center gap-3">
            <button
              type="button"
              onClick={() => {
                setDone(null);
                setTimeout(focusScan, 0);
              }}
              className="rounded-lg border border-white/15 px-4 py-2 text-sm text-zinc-200 transition hover:border-white/30 hover:text-white"
            >
              Nueva venta
            </button>
            <Link
              href={`/admin/orders/${done.order_id}`}
              className="rounded-lg border border-white/[0.08] px-4 py-2 text-sm text-zinc-400 transition hover:border-white/25 hover:text-zinc-200"
            >
              Ver pedido y nota interna
            </Link>
          </div>
        </div>
      </AdminShell>
    );
  }

  return (
    <AdminShell user={ctx.user} dashboard={ctx.dashboard} onSelectCompany={ctx.selectCompany}>
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <label className="text-[11px] uppercase tracking-widest text-zinc-500">
              Sucursal
            </label>
            <select
              value={branch ?? ""}
              onChange={(e) => setBranch(e.target.value ? Number(e.target.value) : null)}
              className="rounded-lg border border-white/[0.08] bg-black/40 px-3 py-1.5 text-sm text-zinc-200 outline-none"
            >
              <option value="">Selecciona…</option>
              {context.branches.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          </div>
          <p className="text-xs text-zinc-600">Vendedor: {context.seller.username}</p>
        </div>

        {branch === null ? (
          <p className="rounded-xl border border-amber-500/20 bg-amber-500/5 px-5 py-4 text-sm text-amber-300">
            Selecciona la sucursal desde la que vas a vender. El stock se descuenta de
            esa sucursal y de ninguna otra.
          </p>
        ) : null}

        <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
          {/* --- entrada --------------------------------------------- */}
          <div className="space-y-4">
            <div>
              <label
                className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-zinc-500"
                htmlFor="pos-scan"
              >
                Escanear código
              </label>
              <input
                id="pos-scan"
                ref={scanRef}
                autoFocus
                autoComplete="off"
                disabled={branch === null}
                className={`${FIELD} font-mono text-base`}
                placeholder="Escanea o escribe el código y pulsa Enter"
                value={scan}
                onChange={(e) => setScan(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    void handleScan(scan);
                  }
                  if (e.key === "Escape") setScan("");
                }}
              />
            </div>

            {feedback ? (
              <p
                className={`rounded-lg px-4 py-2.5 text-sm ${
                  feedback.kind === "ok"
                    ? "border border-emerald-500/20 bg-emerald-500/5 text-emerald-300"
                    : feedback.kind === "warn"
                      ? "border border-amber-500/20 bg-amber-500/5 text-amber-300"
                      : "border border-red-500/20 bg-red-500/5 text-red-400"
                }`}
                role="status"
                aria-live="polite"
              >
                {feedback.text}
              </p>
            ) : null}

            <div>
              <label
                className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-zinc-500"
                htmlFor="pos-search"
              >
                Buscar producto
              </label>
              <input
                id="pos-search"
                autoComplete="off"
                disabled={branch === null}
                className={FIELD}
                placeholder="Por nombre o código"
                value={term}
                onChange={(e) => setTerm(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    setTerm("");
                    focusScan();
                  }
                }}
              />
            </div>

            {found.length ? (
              <div className="overflow-hidden rounded-xl border border-white/[0.06]">
                {found.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => {
                      if (p.available <= 0) {
                        setFeedback({ kind: "warn", text: `${p.name} sin stock aquí.` });
                        return;
                      }
                      addProduct(p);
                      setTerm("");
                      focusScan();
                    }}
                    className="flex w-full items-center justify-between border-b border-white/[0.04] px-4 py-3 text-left text-sm transition last:border-0 hover:bg-white/[0.03]"
                  >
                    <span className="text-zinc-200">{p.name}</span>
                    <span className="flex items-center gap-4 text-xs">
                      <span
                        className={p.available > 0 ? "text-zinc-500" : "text-red-400/80"}
                      >
                        {p.available} disp.
                      </span>
                      <span className="font-mono text-zinc-300">{money(p.price)}</span>
                    </span>
                  </button>
                ))}
              </div>
            ) : null}
          </div>

          {/* --- carrito --------------------------------------------- */}
          <div className="space-y-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
            <div className="flex items-baseline justify-between">
              <p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">
                Carrito
              </p>
              {lines.length ? (
                <button
                  type="button"
                  onClick={() => {
                    if (window.confirm("¿Vaciar el carrito?")) {
                      setLines([]);
                      focusScan();
                    }
                  }}
                  className="text-[11px] text-zinc-600 transition hover:text-zinc-400"
                >
                  Vaciar
                </button>
              ) : null}
            </div>

            {lines.length === 0 ? (
              <p className="py-8 text-center text-sm text-zinc-600">
                Escanea un producto para empezar.
              </p>
            ) : (
              <div className="space-y-2">
                {lines.map((l) => (
                  <div key={l.product} className="rounded-lg bg-black/30 p-3">
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-sm text-zinc-200">{l.name}</p>
                      <button
                        type="button"
                        onClick={() => setQuantity(l.product, 0)}
                        aria-label={`Quitar ${l.name}`}
                        className="text-xs text-zinc-600 transition hover:text-red-400"
                      >
                        ✕
                      </button>
                    </div>
                    <div className="mt-2 flex items-center justify-between">
                      <input
                        type="number"
                        min={1}
                        value={l.quantity}
                        onChange={(e) => setQuantity(l.product, Number(e.target.value))}
                        className="w-20 rounded border border-white/[0.08] bg-black/40 px-2 py-1 text-sm text-zinc-200 outline-none"
                      />
                      <span className="font-mono text-sm text-zinc-300">
                        {money(Number(l.price) * l.quantity)}
                      </span>
                    </div>
                    {l.quantity > l.available ? (
                      <p className="mt-1.5 text-[11px] text-amber-400/90">
                        Sólo hay {l.available} en esta sucursal.
                      </p>
                    ) : null}
                  </div>
                ))}
              </div>
            )}

            <div className="border-t border-white/[0.06] pt-3">
              <label
                className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-zinc-500"
                htmlFor="pos-payment"
              >
                Medio de pago
              </label>
              <select
                id="pos-payment"
                value={payment}
                onChange={(e) => setPayment(e.target.value)}
                className={FIELD}
              >
                {context.payment_methods.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex items-baseline justify-between border-t border-white/[0.06] pt-3">
              <span className="text-xs text-zinc-500">
                {units} unidad{units === 1 ? "" : "es"}
              </span>
              <span className="font-display text-2xl text-white">{money(total)}</span>
            </div>

            <button
              type="button"
              disabled={charging || lines.length === 0 || branch === null}
              onClick={() => void charge()}
              className="w-full rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm font-medium text-emerald-200 transition hover:border-emerald-500/50 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-transparent disabled:text-zinc-600"
            >
              {charging ? "Cobrando…" : "Cobrar"}
            </button>
            <p className="text-center text-[11px] text-zinc-600">
              El precio y el total los calcula el servidor al cobrar.
            </p>
          </div>
        </div>
      </div>
    </AdminShell>
  );
}

export default function PosPage() {
  return <InternalControlGuard>{(ctx) => <PosContent ctx={ctx} />}</InternalControlGuard>;
}
