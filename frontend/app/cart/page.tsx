"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { API_BASE, fetcher } from "../lib/api";
import { fetchWithAuth, getCurrentUser } from "../lib/auth";
import { clearStoredCoupon, emitCartChange, getSessionKey, readStoredCoupon, writeStoredCoupon } from "../lib/cart";
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
  const [coupon, setCoupon] = useState<Coupon | null>(() => readStoredCoupon());
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
      // Las escrituras salen con `fetchWithAuth`: con sesión iniciada el
      // servidor exige el token CSRF, y un `fetch` a secas no lo lleva.
      const res = await fetchWithAuth(`${API_BASE}/cart/${id}/?session_key=${sessionKey}`, {
        method: "PATCH",
        body: JSON.stringify({ quantity }),
      });
      if (!res.ok) throw new Error("No se pudo actualizar.");
      loadCart();
      emitCartChange();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "No se pudo actualizar.");
    }
  }

  async function removeItem(id: number) {
    try {
      const res = await fetchWithAuth(`${API_BASE}/cart/${id}/?session_key=${sessionKey}`, { method: "DELETE" });
      if (!res.ok) throw new Error("No se pudo eliminar.");
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
      const res = await fetchWithAuth(`${API_BASE}/coupons/validate/`, {
        method: "POST",
        body: JSON.stringify({ code: couponInput }),
      });
      const data = await res.json();
      if (!res.ok) {
        setCouponError(data.detail || "Cupón no válido.");
        setCoupon(null);
        clearStoredCoupon();
      } else {
        setCoupon(data);
        setCouponError(null);
        writeStoredCoupon(data);
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
    clearStoredCoupon();
  }

  const unitCount = items.reduce((sum, item) => sum + item.quantity, 0);
  const subtotal = items.reduce((sum, item) => sum + Number(item.product.price) * item.quantity, 0);
  const discountAmount = coupon ? subtotal * (coupon.discount_percent / 100) : 0;
  const total = subtotal - discountAmount;

  return (
    <div className="min-h-screen bg-background px-6 py-10 sm:py-12">
      <div className="mx-auto max-w-6xl">
        <div className="mb-8 flex flex-col gap-3 border-b border-bd-border pb-7 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <span className="section-label">Compras</span>
            <h1 className="mt-2 font-display text-4xl font-semibold tracking-tight text-foreground sm:text-5xl">
              Mi carrito
            </h1>
          </div>
          {unitCount > 0 ? (
            <span className="text-sm text-muted">
              {unitCount} {unitCount === 1 ? "unidad" : "unidades"}
            </span>
          ) : null}
        </div>

        {error ? (
          <div className="mb-6 rounded-2xl border border-danger-border bg-danger-surface p-4 text-sm text-danger" role="alert">
            {error}
          </div>
        ) : null}

        {loading ? (
          <div className="space-y-3" aria-label="Cargando carrito">
            {[1, 2, 3].map((i) => <div key={i} className="h-24 animate-pulse rounded-2xl bg-surface" />)}
          </div>
        ) : items.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-bd-border px-6 py-16 text-center">
            <p className="font-display text-2xl font-extrabold uppercase text-foreground">Tu carrito está vacío</p>
            <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-muted">
              Explora el catálogo y agrega productos para continuar.
            </p>
            <Link
              href="/product"
              className="mt-6 inline-flex rounded-full bg-foreground px-6 py-3 text-xs font-bold uppercase tracking-[0.08em] text-background transition hover:opacity-90"
            >
              Ver catálogo
            </Link>
          </div>
        ) : (
          <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_360px]">
            <div className="space-y-3">
              {isLoggedIn === false ? (
                <div className="rounded-xl border border-bd-border bg-surface p-4 text-sm text-muted">
                  Inicia sesión para mantener tu experiencia vinculada a tu cuenta.{" "}
                  <Link href="/auth" className="font-bold text-foreground underline underline-offset-4">Ingresar</Link>
                </div>
              ) : null}

              {items.map((item) => (
                <CartItemCard
                  key={item.id}
                  {...item}
                  onQuantityChange={(q) => updateItem(item.id, q)}
                  onRemove={() => removeItem(item.id)}
                />
              ))}
            </div>

            <aside className="h-fit rounded-2xl border border-bd-border bg-surface p-6 lg:sticky lg:top-24">
              <h2 className="font-display text-xl font-extrabold uppercase text-foreground">Resumen</h2>

              <div className="mt-5">
                <label htmlFor="cart-coupon" className="mb-2 block text-[10px] font-bold uppercase tracking-[0.1em] text-muted">
                  Cupón
                </label>
                {coupon ? (
                  <div className="flex items-center justify-between rounded-xl border border-bd-border bg-background px-4 py-3">
                    <div>
                      <span className="text-sm font-bold text-foreground">{coupon.code}</span>
                      <span className="ml-2 text-sm text-muted">−{coupon.discount_percent}%</span>
                    </div>
                    <button type="button" onClick={removeCoupon} className="text-xs text-muted transition hover:text-danger">
                      Quitar
                    </button>
                  </div>
                ) : (
                  <div className="flex gap-2">
                    <input
                      id="cart-coupon"
                      name="coupon"
                      type="text"
                      autoComplete="off"
                      value={couponInput}
                      onChange={(e) => setCouponInput(e.target.value.toUpperCase())}
                      onKeyDown={(e) => e.key === "Enter" && validateCoupon()}
                      placeholder="Código"
                      className="min-w-0 flex-1 rounded-xl border border-bd-border bg-background px-3 py-2.5 text-sm text-foreground placeholder:text-muted/60 focus:border-foreground/25 focus:outline-none"
                    />
                    <button
                      type="button"
                      onClick={validateCoupon}
                      disabled={couponLoading || !couponInput.trim()}
                      className="rounded-xl border border-bd-border px-3 py-2 text-xs font-bold uppercase tracking-[0.06em] text-foreground transition hover:border-foreground/25 disabled:opacity-40"
                    >
                      {couponLoading ? "..." : "Aplicar"}
                    </button>
                  </div>
                )}
                {couponError ? <p className="mt-2 text-xs text-danger">{couponError}</p> : null}
              </div>

              <dl className="mt-6 space-y-3 border-t border-bd-border pt-5 text-sm">
                <div className="flex justify-between gap-4">
                  <dt className="text-muted">Subtotal</dt>
                  <dd className="font-medium tabular-nums text-foreground">S/ {formatMoney(subtotal)}</dd>
                </div>
                {coupon ? (
                  <div className="flex justify-between gap-4">
                    <dt className="text-muted">Descuento ({coupon.discount_percent}%)</dt>
                    <dd className="font-medium tabular-nums text-foreground">−S/ {formatMoney(discountAmount)}</dd>
                  </div>
                ) : null}
                <div className="flex justify-between gap-4">
                  <dt className="text-muted">Envío</dt>
                  <dd className="text-muted">A calcular</dd>
                </div>
              </dl>

              <div className="mt-5 flex items-end justify-between border-t border-bd-border pt-5">
                <span className="font-display text-sm font-extrabold uppercase text-foreground">Total</span>
                <span className="font-display text-2xl font-extrabold tabular-nums text-foreground">
                  S/ {formatMoney(total)}
                </span>
              </div>

              <Link
                href="/checkout"
                className="mt-6 block w-full rounded-full bg-foreground py-3.5 text-center text-xs font-bold uppercase tracking-[0.08em] text-background transition hover:opacity-90"
              >
                Continuar al checkout
              </Link>
              <Link
                href="/product"
                className="mt-3 block w-full rounded-xl border border-bd-border py-3 text-center text-xs font-bold text-muted transition hover:border-foreground/25 hover:text-foreground"
              >
                Seguir comprando
              </Link>
            </aside>
          </div>
        )}
      </div>
    </div>
  );
}
