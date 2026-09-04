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
 *   THE TERMS BOX IS TICKED BY A PERSON, EVERY TIME. The order records that
 *   the conditions and the warranty policy were explained and accepted, and
 *   the first version asserted that automatically on the grounds that handing
 *   the article over implies it. It does not — nobody had said anything to the
 *   customer. What is recorded now is a statement the operator actually made,
 *   and the audit trail already names them.
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
  fetchCombos,
  fetchCustomers,
  fetchPosContext,
  posLookup,
  posPreview,
  posSale,
  posSearch,
  type ComboOffer,
  type CustomerRow,
  type PosContext,
  type PosPreview,
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

/**
 * Round-number notes an operator is likely to be handed, for THIS total.
 *
 * Computed rather than hardcoded: a fixed ladder that suits a phone sale is
 * useless for a cable, and a SaaS cannot assume either price range.
 */
function cashSuggestions(total: number): number[] {
  if (!Number.isFinite(total) || total <= 0) return [];
  const exact = Math.ceil(total * 100) / 100;
  const out = new Set<number>([exact]);
  for (const step of [10, 20, 50, 100, 200]) {
    const up = Math.ceil(total / step) * step;
    if (up >= total) out.add(up);
    if (out.size >= 4) break;
  }
  return [...out].sort((a, b) => a - b).slice(0, 4);
}

const FIELD =
  "w-full rounded-lg border border-bd-border bg-background/40 px-3 py-2 text-sm text-foreground outline-none transition focus:border-bd-border disabled:opacity-50";

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
  // Unticked on every new basket. The record this produces says a person
  // confirmed they explained the terms, so it has to be a person's act.
  const [terms, setTerms] = useState(false);

  const [scan, setScan] = useState("");
  const [term, setTerm] = useState("");
  const [found, setFound] = useState<PosProduct[]>([]);

  // --- the enriched sale ---------------------------------------------------
  const [customer, setCustomer] = useState<CustomerRow | null>(null);
  const [customerTerm, setCustomerTerm] = useState("");
  const [customerHits, setCustomerHits] = useState<CustomerRow[]>([]);
  const [pickingCustomer, setPickingCustomer] = useState(false);

  const [seller, setSeller] = useState<number | null>(null);

  const [couponCode, setCouponCode] = useState("");
  const [manualType, setManualType] = useState<"" | "percent" | "amount">("");
  const [manualValue, setManualValue] = useState("");
  const [manualReason, setManualReason] = useState("");

  const [received, setReceived] = useState("");
  const [paymentReference, setPaymentReference] = useState("");
  const [saleNotes, setSaleNotes] = useState("");

  // Priced BY THE SERVER. The browser shows what the till will actually
  // charge, rather than a number it computed itself and hoped matched.
  const [preview, setPreview] = useState<PosPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  // Combos this branch can actually complete right now.
  const [combos, setCombos] = useState<ComboOffer[]>([]);

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
        setSeller(c.seller.id);
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

  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      if (branch === null) {
        setCombos([]);
        return;
      }
      void (async () => {
        const offers = await fetchCombos(companyId, branch);
        if (!cancelled) setCombos(offers);
      })();
    }, 0);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [branch, companyId, done]);

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

  // Customer search, debounced like the product one.
  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      if (customerTerm.trim().length < 2) {
        setCustomerHits([]);
        return;
      }
      void (async () => {
        try {
          const page = await fetchCustomers(companyId, { search: customerTerm.trim() });
          if (!cancelled) setCustomerHits(page.results.slice(0, 8));
        } catch {
          if (!cancelled) setCustomerHits([]);
        }
      })();
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [customerTerm, companyId]);

  // THE TOTAL COMES FROM THE SERVER.
  //
  // Recomputing a coupon percentage in the browser would mean the number the
  // operator reads aloud is produced by different code from the number that is
  // charged — and the customer is standing there when they disagree.
  useEffect(() => {
    let cancelled = false;
    if (branch === null || lines.length === 0) {
      const timer = setTimeout(() => {
        setPreview(null);
        setPreviewError(null);
      }, 0);
      return () => clearTimeout(timer);
    }
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const p = await posPreview(companyId, {
            branch,
            items: lines.map((l) => ({ product: l.product, quantity: l.quantity })),
            customer: customer?.id ?? null,
            seller,
            payment_method: payment,
            coupon_code: couponCode.trim(),
            manual_discount_type: manualType,
            manual_discount_value: manualValue || null,
            discount_reason: manualReason,
          });
          if (!cancelled) {
            setPreview(p);
            setPreviewError(null);
          }
        } catch (err) {
          if (!cancelled) {
            setPreview(null);
            setPreviewError(err instanceof Error ? err.message : "No se pudo calcular.");
          }
        }
      })();
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [
    branch, lines, customer, seller, payment, couponCode,
    manualType, manualValue, manualReason, companyId,
  ]);

  const subtotal = lines.reduce((sum, l) => sum + Number(l.price) * l.quantity, 0);
  const total = preview ? Number(preview.total) : subtotal;
  const discount = preview ? Number(preview.discount) : 0;
  const units = lines.reduce((sum, l) => sum + l.quantity, 0);
  const isCash = payment === "cash";
  const change = isCash && received ? Number(received) - total : null;

  function setQuantity(productId: number, quantity: number) {
    setLines((prev) =>
      quantity <= 0
        ? prev.filter((l) => l.product !== productId)
        : prev.map((l) => (l.product === productId ? { ...l, quantity } : l)),
    );
  }

  async function charge() {
    if (charging || !lines.length || branch === null || !terms) return;
    if (isCash && (received === "" || Number(received) < total)) return;
    setCharging(true);
    setFeedback(null);
    try {
      const result = await posSale(companyId, {
        branch,
        items: lines.map((l) => ({ product: l.product, quantity: l.quantity })),
        customer: customer?.id ?? null,
        seller,
        payment_method: payment,
        idempotency_key: keyRef.current,
        terms_confirmed: terms,
        coupon_code: couponCode.trim(),
        manual_discount_type: manualType,
        manual_discount_value: manualValue || null,
        discount_reason: manualReason,
        amount_received: isCash ? received : null,
        payment_reference: paymentReference,
        sale_notes: saleNotes,
      });
      setDone(result);
      setLines([]);
      setTerms(false);
      setCustomer(null);
      setCouponCode("");
      setManualType("");
      setManualValue("");
      setManualReason("");
      setReceived("");
      setPaymentReference("");
      setSaleNotes("");
      setPreview(null);
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
        <p className="py-8 text-sm text-muted">Abriendo caja…</p>
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
          <p className="font-display text-3xl text-foreground">{money(done.total)}</p>
          <div className="space-y-1 rounded-xl border border-bd-border bg-surface p-5 text-left text-sm text-muted">
            <p>Pedido #{done.order_id}</p>
            <p>Cliente: {done.customer || "Sin identificar"}</p>
            <p>Vendedor: {done.seller || "—"}</p>
            <p>Sucursal: {done.branch.name}</p>
            <p className="pt-2">Subtotal: {money(done.subtotal)}</p>
            {Number(done.discount) > 0 ? (
              <p>
                Descuento: −{money(done.discount)}
                {done.discount_reason ? ` (${done.discount_reason})` : ""}
              </p>
            ) : null}
            <p className="text-foreground">Total: {money(done.total)}</p>
            <p className="pt-2">
              Medio de pago:{" "}
              {context.payment_methods.find((m) => m.value === done.payment_method)?.label ??
                done.payment_method}
            </p>
            {/* Cash and change ONLY when cash was involved — a card sale has
                no change, and printing a zero would imply otherwise. */}
            {done.amount_received !== null ? (
              <>
                <p>Efectivo recibido: {money(done.amount_received)}</p>
                <p className="text-foreground">Vuelto: {money(done.change_amount ?? "0")}</p>
              </>
            ) : null}
            {done.payment_reference ? <p>Referencia: {done.payment_reference}</p> : null}
            {/* Null unless this operator may see earnings. */}
            {done.commission ? (
              <p className="pt-2 text-muted">Comisión: {money(done.commission)}</p>
            ) : null}
          </div>
          <div className="flex flex-wrap justify-center gap-3">
            <button
              type="button"
              onClick={() => {
                setDone(null);
                setTimeout(focusScan, 0);
              }}
              className="rounded-lg border border-bd-border px-4 py-2 text-sm text-foreground transition hover:border-bd-border hover:text-foreground"
            >
              Nueva venta
            </button>
            <Link
              href={`/admin/orders/${done.order_id}`}
              className="rounded-lg border border-bd-border px-4 py-2 text-sm text-muted transition hover:border-bd-border hover:text-foreground"
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
            <label className="text-[11px] uppercase tracking-widest text-muted">
              Sucursal
            </label>
            <select
              value={branch ?? ""}
              onChange={(e) => setBranch(e.target.value ? Number(e.target.value) : null)}
              className="rounded-lg border border-bd-border bg-background/40 px-3 py-1.5 text-sm text-foreground outline-none"
            >
              <option value="">Selecciona…</option>
              {context.branches.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          </div>
          <p className="text-xs text-muted">Vendedor: {context.seller.username}</p>
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
                className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted"
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
                className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted"
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

            {combos.length && lines.length === 0 ? (
              <div className="space-y-2">
                <p className="text-[11px] font-semibold uppercase tracking-widest text-muted">
                  Combos disponibles
                </p>
                {combos.map((combo) => (
                  <button
                    key={combo.id}
                    type="button"
                    disabled={combo.available_sets < 1}
                    onClick={() => {
                      // Adds the REAL components. The same promotion engine
                      // then detects them — there is no second discount path.
                      setLines(
                        combo.components.map((c) => ({
                          product: c.product_id,
                          name: c.product_name,
                          price: c.price,
                          quantity: c.quantity,
                          available: c.available,
                        })),
                      );
                      focusScan();
                    }}
                    className="flex w-full items-center justify-between rounded-lg border border-bd-border px-4 py-3 text-left text-sm transition hover:border-bd-border disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <span>
                      <span className="text-foreground">{combo.name}</span>
                      <span className="block text-[11px] text-muted">
                        {combo.components
                          .map((c) => `${c.quantity}× ${c.product_name}`)
                          .join(" + ")}
                      </span>
                    </span>
                    <span className="text-right">
                      <span className="block font-mono text-foreground/85">
                        {money(combo.combo_amount)}
                      </span>
                      <span className="block text-[11px] text-emerald-400/80">
                        ahorro {money(combo.discount_amount)}
                      </span>
                      {combo.available_sets < 1 ? (
                        <span className="block text-[11px] text-red-400/80">
                          sin stock para completarlo
                        </span>
                      ) : null}
                    </span>
                  </button>
                ))}
              </div>
            ) : null}

            {found.length ? (
              <div className="overflow-hidden rounded-xl border border-bd-border">
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
                    className="flex w-full items-center justify-between border-b border-bd-border px-4 py-3 text-left text-sm transition last:border-0 hover:bg-surface"
                  >
                    <span className="text-foreground">{p.name}</span>
                    <span className="flex items-center gap-4 text-xs">
                      <span
                        className={p.available > 0 ? "text-muted" : "text-red-400/80"}
                      >
                        {p.available} disp.
                      </span>
                      <span className="font-mono text-foreground/85">{money(p.price)}</span>
                    </span>
                  </button>
                ))}
              </div>
            ) : null}
          </div>

          {/* --- carrito --------------------------------------------- */}
          <div className="space-y-3 rounded-xl border border-bd-border bg-surface p-4">
            <div className="flex items-baseline justify-between">
              <p className="text-[11px] font-semibold uppercase tracking-widest text-muted">
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
                  className="text-[11px] text-muted transition hover:text-muted"
                >
                  Vaciar
                </button>
              ) : null}
            </div>

            {lines.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted">
                Escanea un producto para empezar.
              </p>
            ) : (
              <div className="space-y-2">
                {lines.map((l) => (
                  <div key={l.product} className="rounded-lg bg-background/30 p-3">
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-sm text-foreground">{l.name}</p>
                      <button
                        type="button"
                        onClick={() => setQuantity(l.product, 0)}
                        aria-label={`Quitar ${l.name}`}
                        className="text-xs text-muted transition hover:text-red-400"
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
                        className="w-20 rounded border border-bd-border bg-background/40 px-2 py-1 text-sm text-foreground outline-none"
                      />
                      <span className="font-mono text-sm text-foreground/85">
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

            {/* --- cliente --------------------------------------------- */}
            <div className="border-t border-bd-border pt-3">
              <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-widest text-muted">
                Cliente
              </p>
              {customer ? (
                <div className="flex items-center justify-between rounded-lg bg-background/30 px-3 py-2 text-sm">
                  <span className="text-foreground">{customer.display_name}</span>
                  <button
                    type="button"
                    onClick={() => setCustomer(null)}
                    className="text-xs text-muted transition hover:text-red-400"
                  >
                    Quitar
                  </button>
                </div>
              ) : pickingCustomer ? (
                <div className="space-y-2">
                  <input
                    autoFocus
                    className={FIELD}
                    placeholder="Nombre, documento o teléfono"
                    value={customerTerm}
                    onChange={(e) => setCustomerTerm(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Escape") {
                        setPickingCustomer(false);
                        setCustomerTerm("");
                        focusScan();
                      }
                    }}
                  />
                  {customerHits.map((c) => (
                    <button
                      key={c.id}
                      type="button"
                      onClick={() => {
                        setCustomer(c);
                        setPickingCustomer(false);
                        setCustomerTerm("");
                        focusScan();
                      }}
                      className="block w-full rounded bg-background/30 px-3 py-2 text-left text-sm text-foreground/85 transition hover:bg-surface"
                    >
                      {c.display_name}
                      {c.document_number ? (
                        <span className="ml-2 font-mono text-[11px] text-muted">
                          {c.document_number}
                        </span>
                      ) : null}
                    </button>
                  ))}
                  <div className="flex gap-3 text-xs">
                    <button
                      type="button"
                      onClick={() => {
                        setPickingCustomer(false);
                        setCustomerTerm("");
                        focusScan();
                      }}
                      className="text-muted transition hover:text-muted"
                    >
                      Cancelar
                    </button>
                    {context.can_manage_customers ? (
                      <Link
                        href="/admin/customers"
                        className="text-muted underline underline-offset-2 transition hover:text-foreground/85"
                      >
                        Nuevo cliente
                      </Link>
                    ) : null}
                  </div>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => setPickingCustomer(true)}
                  className="w-full rounded-lg border border-dashed border-bd-border px-3 py-2 text-sm text-muted transition hover:border-bd-border hover:text-foreground/85"
                >
                  Buscar cliente (opcional)
                </button>
              )}
            </div>

            {/* --- vendedor -------------------------------------------- */}
            <div className="border-t border-bd-border pt-3">
              <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-widest text-muted">
                Vendedor
              </p>
              {context.can_assign_seller && context.sellers.length ? (
                <select
                  value={seller ?? ""}
                  onChange={(e) => setSeller(e.target.value ? Number(e.target.value) : null)}
                  className={FIELD}
                >
                  {context.sellers.map((sl) => (
                    <option key={sl.id} value={sl.id}>
                      {sl.name}
                    </option>
                  ))}
                </select>
              ) : (
                // Read-only when the operator may not reassign: offering a
                // control the backend would refuse is worse than not offering it.
                <p className="text-sm text-foreground/85">{context.seller.name}</p>
              )}
            </div>

            {/* --- descuento ------------------------------------------- */}
            <div className="space-y-2 border-t border-bd-border pt-3">
              <p className="text-[11px] font-semibold uppercase tracking-widest text-muted">
                Descuento
              </p>
              {preview?.promotions?.length ? (
                <p className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-xs text-emerald-300">
                  Una promoción automática ya está aplicada. No se combina con
                  códigos ni descuentos manuales.
                </p>
              ) : null}
              <input
                className={FIELD}
                placeholder="Código promocional"
                value={couponCode}
                disabled={manualType !== "" || Boolean(preview?.promotions?.length)}
                onChange={(e) => setCouponCode(e.target.value)}
              />
              {context.can_apply_discount ? (
                <>
                  <div className="flex gap-2">
                    <select
                      value={manualType}
                      disabled={couponCode.trim() !== ""}
                      onChange={(e) => {
                        setManualType(e.target.value as "" | "percent" | "amount");
                        if (!e.target.value) {
                          setManualValue("");
                          setManualReason("");
                        }
                      }}
                      className={`${FIELD} flex-1`}
                    >
                      <option value="">Sin descuento manual</option>
                      <option value="percent">Porcentaje</option>
                      <option value="amount">Monto</option>
                    </select>
                    {manualType ? (
                      <input
                        type="number"
                        min={0}
                        className={`${FIELD} w-28`}
                        placeholder={manualType === "percent" ? "%" : "S/"}
                        value={manualValue}
                        onChange={(e) => setManualValue(e.target.value)}
                      />
                    ) : null}
                  </div>
                  {manualType ? (
                    <input
                      className={FIELD}
                      placeholder="Motivo del descuento (obligatorio)"
                      maxLength={200}
                      value={manualReason}
                      onChange={(e) => setManualReason(e.target.value)}
                    />
                  ) : null}
                </>
              ) : null}
              {previewError ? (
                <p className="text-xs text-red-400">{previewError}</p>
              ) : null}
            </div>

            <div className="border-t border-bd-border pt-3">
              <label
                className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted"
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

            {/* --- efectivo -------------------------------------------- */}
            {isCash ? (
              <div className="border-t border-bd-border pt-3">
                <label
                  className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted"
                  htmlFor="pos-received"
                >
                  Efectivo recibido
                </label>
                <input
                  id="pos-received"
                  type="number"
                  min={0}
                  step="0.01"
                  className={FIELD}
                  value={received}
                  onChange={(e) => setReceived(e.target.value)}
                />
                <div className="mt-2 flex flex-wrap gap-2">
                  {/* Calculated from THIS total, not a hardcoded ladder of
                      amounts that only suits one price range. */}
                  {cashSuggestions(total).map((amount) => (
                    <button
                      key={amount}
                      type="button"
                      onClick={() => setReceived(String(amount))}
                      className="rounded border border-bd-border px-2.5 py-1 text-xs text-muted transition hover:border-bd-border hover:text-foreground"
                    >
                      {money(amount)}
                    </button>
                  ))}
                </div>
                {change !== null && change >= 0 ? (
                  <p className="mt-2 text-sm text-muted">
                    Vuelto: <span className="text-foreground">{money(change)}</span>
                  </p>
                ) : received !== "" ? (
                  <p className="mt-2 text-sm text-amber-400/90">
                    El efectivo no alcanza para el total.
                  </p>
                ) : null}
              </div>
            ) : (
              <div className="border-t border-bd-border pt-3">
                <label
                  className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted"
                  htmlFor="pos-ref"
                >
                  Referencia del pago (opcional)
                </label>
                <input
                  id="pos-ref"
                  className={FIELD}
                  maxLength={100}
                  placeholder="N.º de operación o autorización"
                  value={paymentReference}
                  onChange={(e) => setPaymentReference(e.target.value)}
                />
              </div>
            )}

            <div className="space-y-1 border-t border-bd-border pt-3 text-sm">
              <div className="flex justify-between text-muted">
                <span>Subtotal</span>
                <span className="font-mono">{money(subtotal)}</span>
              </div>
              {/* A promotion is NAMED, because an unexplained reduction on a
                  till display is something an operator cannot answer for. */}
              {preview?.promotions?.length
                ? preview.promotions.map((p) => (
                    <div key={p.id} className="flex justify-between text-emerald-400/80">
                      <span>
                        ✓ {p.name}
                        {p.applications > 1 ? ` ×${p.applications}` : ""}
                      </span>
                      <span className="font-mono">−{money(p.discount_amount)}</span>
                    </div>
                  ))
                : discount > 0 ? (
                    <div className="flex justify-between text-emerald-400/80">
                      <span>
                        Descuento
                        {preview?.discount_source === "coupon" ? " (cupón)" : ""}
                      </span>
                      <span className="font-mono">−{money(discount)}</span>
                    </div>
                  ) : null}
              <div className="flex items-baseline justify-between pt-1">
                <span className="text-xs text-muted">
                  {units} unidad{units === 1 ? "" : "es"}
                </span>
                <span className="font-display text-2xl text-foreground">{money(total)}</span>
              </div>
              {preview?.commission ? (
                <p className="pt-1 text-right text-[11px] text-muted">
                  Comisión estimada: {money(preview.commission.amount)}
                </p>
              ) : null}
            </div>

            <div className="border-t border-bd-border pt-3">
              <label
                className="mb-1.5 block text-[11px] font-semibold uppercase tracking-widest text-muted"
                htmlFor="pos-notes"
              >
                Observaciones (opcional)
              </label>
              <textarea
                id="pos-notes"
                rows={2}
                maxLength={1000}
                className={FIELD}
                placeholder="Nota interna sobre esta venta"
                value={saleNotes}
                onChange={(e) => setSaleNotes(e.target.value)}
              />
              <p className="mt-1 text-[11px] text-muted">
                Sobre esta venta, no sobre el cliente. Sólo control interno.
              </p>
            </div>

            <label className="flex items-start gap-2 border-t border-bd-border pt-3 text-xs text-muted">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={terms}
                disabled={charging}
                onChange={(e) => setTerms(e.target.checked)}
              />
              <span>
                Confirmo que informé al cliente las condiciones de venta y la
                política de garantía, y que fueron aceptadas.
              </span>
            </label>

            <button
              type="button"
              disabled={
                charging ||
                lines.length === 0 ||
                branch === null ||
                !terms ||
                previewError !== null ||
                (isCash && (received === "" || Number(received) < total))
              }
              onClick={() => void charge()}
              className="w-full rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm font-medium text-emerald-200 transition hover:border-emerald-500/50 disabled:cursor-not-allowed disabled:border-bd-border disabled:bg-transparent disabled:text-muted"
            >
              {charging ? "Cobrando…" : "Cobrar"}
            </button>
            <p className="text-center text-[11px] text-muted">
              El precio, el descuento y el total los calcula el servidor.
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
