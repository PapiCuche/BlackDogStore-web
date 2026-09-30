"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { API_BASE, fetcher } from "../lib/api";
import { getCurrentUser } from "../lib/auth";
import { getSessionKey, emitCartChange } from "../lib/cart";
import { formatMoney } from "../lib/format";
import { CartItemCard } from "../components/CartItemCard";

type CartItem = {
  id: number;
  quantity: number;
  product: {
    id: number;
    name: string;
    price: number | string;
    slug: string;
    image_url?: string;
  };
};

type Coupon = { code: string; discount_percent: number };

export default function CartPage() {
  const [items, setItems] = useState<CartItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isLoggedIn, setIsLoggedIn] = useState<boolean | null>(null);
  const sessionKey = getSessionKey();

  const [couponInput, setCouponInput] = useState("");
  const [coupon, setCoupon] = useState<Coupon | null>(() => {
    if (typeof window === "undefined") return null;
    const saved = sessionStorage.getItem("blackdog_coupon");
    return saved ? (JSON.parse(saved) as Coupon) : null;
  });
  const [couponError, setCouponError] = useState<string | null>(null);
  const [couponLoading, setCouponLoading] = useState(false);

  async function loadCart() {
    setLoading(true);
    setError(null);
    try {
      const data = await fetcher<CartItem[]>(`${API_BASE}/cart/?session_key=${sessionKey}`);
      setItems(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Error al cargar el carrito.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadCart();
    getCurrentUser().then((u) => setIsLoggedIn(Boolean(u)));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionKey]);

  async function updateItem(id: number, quantity: number) {
    try {
      await fetch(`${API_BASE}/cart/${id}/?session_key=${sessionKey}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ quantity }),
      });
      loadCart();
      emitCartChange();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "No se pudo actualizar.");
    }
  }

  async function removeItem(id: number) {
    try {
      await fetch(`${API_BASE}/cart/${id}/?session_key=${sessionKey}`, { method: "DELETE" });
      loadCart();
      emitCartChange();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "No se pudo eliminar.");
    }
  }

  async function validateCoupon() {
    if (!couponInput.trim()) return;
    setCouponLoading(true);
    setCouponError(null);
    try {
      const res = await fetch(`${API_BASE}/coupons/validate/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: couponInput }),
      });
      const data = await res.json();
      if (!res.ok) {
        setCouponError(data.detail || "Cupón no válido.");
        setCoupon(null);
        sessionStorage.removeItem("blackdog_coupon");
      } else {
        setCoupon(data);
        setCouponError(null);
        sessionStorage.setItem("blackdog_coupon", JSON.stringify(data));
        setCouponInput("");
      }
    } catch {
      setCouponError("Error al validar el cupón.");
    } finally {
      setCouponLoading(false);
    }
  }

  function removeCoupon() {
    setCoupon(null);
    setCouponError(null);
    sessionStorage.removeItem("blackdog_coupon");
  }

  const subtotal = items.reduce((sum, item) => sum + Number(item.product.price) * item.quantity, 0);
  const discountAmount = coupon ? subtotal * (coupon.discount_percent / 100) : 0;
  const total = subtotal - discountAmount;

  return (
    <div className="min-h-screen bg-background px-6 py-12 text-foreground">
      <div className="mx-auto max-w-5xl">
        <div className="mb-8 flex items-end justify-between">
          <div>
            <span className="section-label">Compras</span>
            <h1 className="font-display mt-2 text-5xl font-black uppercase tracking-tight text-foreground">Mi carrito</h1>
          </div>
          {items.length > 0 && (
            <span className="text-sm text-muted-foreground">{items.length} {items.length === 1 ? "producto" : "productos"}</span>
          )}
        </div>

        {error && (
          <div className="mb-6 rounded-2xl border border-red-500/20 bg-red-500/[0.08] p-4 text-sm text-red-400">{error}</div>
        )}

        {loading ? (
          <div className="space-y-3">
            {[1, 2, 3].map((i) => <div key={i} className="h-24 animate-pulse rounded-xl bg-surface" />)}
          </div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center gap-6 rounded-2xl border border-dashed border-bd-border p-10 text-center sm:p-16">
            <div className="flex h-14 w-14 items-center justify-center rounded-xl border border-bd-border bg-surface text-muted-foreground" aria-hidden="true">
              <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.4} d="M3 3h2l.4 2M7 13h10l4-8H5.4M7 13l-1.4 7h12.8M17 21a1 1 0 100-2 1 1 0 000 2zM9 21a1 1 0 100-2 1 1 0 000 2z" />
              </svg>
            </div>
            <div>
              <p className="font-display text-2xl font-black uppercase text-muted-foreground">Tu carrito está vacío</p>
              <p className="mt-1 text-sm text-muted-foreground">Explora el catálogo y agrega productos para continuar.</p>
            </div>
            <Link
              href="/product"
              className="rounded-xl bg-primary px-6 py-3 text-xs font-black uppercase tracking-[0.12em] text-background transition hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              Ver catálogo
            </Link>
          </div>
        ) : (
          <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
            {/* Items */}
            <div className="space-y-3">
              {isLoggedIn === false && (
                <div className="rounded-xl border border-bd-border bg-surface p-4 text-sm text-muted-foreground">
                  Inicia sesión para guardar tu carrito.{" "}
                  <Link href="/auth" className="font-bold text-foreground underline underline-offset-4">Crear cuenta</Link>
                </div>
              )}
              {items.map((item) => (
                <CartItemCard
                  key={item.id}
                  {...item}
                  onQuantityChange={(q) => updateItem(item.id, q)}
                  onRemove={() => removeItem(item.id)}
                />
              ))}
            </div>

            {/* Summary */}
            <div className="h-fit space-y-4">
              <div className="rounded-xl border border-bd-border bg-surface p-6">
                <h2 className="font-display text-xl font-black uppercase text-foreground">Resumen del pedido</h2>

                {/* Coupon input */}
                <div className="mt-5">
                  <label className="mb-2 block text-[10px] font-bold uppercase tracking-widest text-muted-foreground">Cupón de descuento</label>
                  {coupon ? (
                    <div className="flex items-center justify-between rounded-xl border border-bd-border bg-background px-4 py-3">
                      <div>
                        <span className="text-sm font-bold text-foreground">{coupon.code}</span>
                        <span className="ml-2 text-sm text-muted-foreground">−{coupon.discount_percent}%</span>
                      </div>
                      <button onClick={removeCoupon} className="text-xs text-muted-foreground transition hover:text-red-400">✕ Quitar</button>
                    </div>
                  ) : (
                    <div className="flex gap-2">
                      <input
                        type="text"
                        value={couponInput}
                        onChange={(e) => setCouponInput(e.target.value.toUpperCase())}
                        onKeyDown={(e) => e.key === "Enter" && validateCoupon()}
                        placeholder="Ej: APPLE10"
                        className="flex-1 rounded-xl border border-bd-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:border-accent/60 focus:outline-none"
                      />
                      <button
                        onClick={validateCoupon}
                        disabled={couponLoading || !couponInput.trim()}
                        className="rounded-xl border border-bd-border bg-background px-3 py-2 text-xs font-bold uppercase tracking-widest text-muted-foreground transition hover:border-accent/60 hover:text-foreground disabled:opacity-40"
                      >
                        {couponLoading ? "..." : "Aplicar"}
                      </button>
                    </div>
                  )}
                  {couponError && <p className="mt-1.5 text-xs text-red-400">{couponError}</p>}
                </div>

                {/* Totals */}
                <div className="mt-5 space-y-2 text-sm text-muted-foreground">
                  <div className="flex justify-between">
                    <span>Subtotal</span>
                    <span className="text-foreground">S/ {formatMoney(subtotal)}</span>
                  </div>
                  {coupon && (
                    <div className="flex justify-between text-foreground">
                      <span>Descuento ({coupon.discount_percent}%)</span>
                      <span>−S/ {formatMoney(discountAmount)}</span>
                    </div>
                  )}
                  <div className="flex justify-between">
                    <span>Envío</span>
                    <span className="text-muted-foreground">A calcular</span>
                  </div>
                </div>

                <div className="mt-4 border-t border-bd-border pt-4">
                  <div className="flex justify-between">
                    <span className="font-display font-black uppercase text-foreground">Total</span>
                    <span className="font-display text-xl font-black text-foreground">S/ {formatMoney(total)}</span>
                  </div>
                </div>

                <Link
                  href="/checkout"
                  className="mt-6 block w-full rounded-xl bg-primary py-3.5 text-center text-xs font-black uppercase tracking-[0.12em] text-background transition hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  Ir al checkout →
                </Link>
                <Link
                  href="/product"
                  className="mt-3 block w-full rounded-xl border border-bd-border bg-background py-3 text-center text-xs font-bold uppercase tracking-[0.1em] text-muted-foreground transition hover:border-accent/40 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  Seguir comprando
                </Link>
              </div>

              <div className="rounded-xl border border-bd-border bg-background p-4 text-xs leading-5 text-muted-foreground">
                La entrega y sus condiciones se confirman en el siguiente paso antes de iniciar el pago.
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
