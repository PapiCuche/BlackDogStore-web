"use client";

import { useEffect, useState } from "react";
import { useStorefront } from "../components/StorefrontProvider";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { API_BASE } from "../lib/api";
import { getCurrentUser, fetchWithAuth } from "../lib/auth";
import { formatMoney } from "../lib/format";

type OrderItem = {
  id: number;
  product: { name: string; slug: string; price: number; image_url?: string };
  quantity: number;
  price: number | string;
};

type Order = {
  id: number;
  customer_name: string;
  customer_email: string;
  total: number | string;
  discount_amount: number | string;
  coupon_code: string;
  paid: boolean;
  created_at: string;
  items: OrderItem[];
};

export default function OrdersPage() {
  const whatsappLink = useStorefront().contact.whatsapp_link;
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedOrder, setExpandedOrder] = useState<number | null>(null);
  const router = useRouter();

  useEffect(() => {
    let cancelled = false;

    async function load() {
      const user = await getCurrentUser();
      if (!user) {
        router.push("/auth");
        return;
      }

      try {
        const res = await fetchWithAuth(`${API_BASE}/orders/`);
        if (!res.ok) {
          const body = await res.json().catch(() => null);
          throw new Error(body?.detail || "No se pudieron cargar tus pedidos.");
        }
        const data = await res.json();
        if (!cancelled) {
          setOrders(Array.isArray(data) ? data : []);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "No se pudieron cargar tus pedidos.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [router]);

  function toggleOrder(id: number) {
    setExpandedOrder((previous) => previous === id ? null : id);
  }

  return (
    <div className="min-h-screen bg-background px-6 py-10 sm:py-12">
      <div className="mx-auto max-w-5xl">
        <header className="mb-8 border-b border-bd-border pb-7">
          <span className="section-label">Cuenta</span>
          <h1 className="mt-2 font-display text-4xl font-semibold tracking-tight text-foreground sm:text-5xl">
            Mis pedidos
          </h1>
          <p className="mt-3 max-w-xl text-sm leading-6 text-muted">
            Revisa las órdenes asociadas a tu cuenta y el estado de pago registrado.
          </p>
        </header>

        {loading ? (
          <div className="space-y-3" aria-label="Cargando pedidos">
            {[1, 2, 3].map((i) => <div key={i} className="h-24 animate-pulse rounded-2xl bg-surface" />)}
          </div>
        ) : error ? (
          <div className="rounded-2xl border border-danger-border bg-danger-surface px-5 py-6 text-sm text-danger" role="alert">
            <p className="font-semibold">No pudimos cargar tus pedidos.</p>
            <p className="mt-1 text-danger">{error}</p>
          </div>
        ) : orders.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-bd-border px-6 py-14 text-center">
            <p className="font-display text-2xl font-extrabold uppercase text-foreground">Todavía no tienes pedidos</p>
            <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-muted">Cuando completes una compra, su orden aparecerá aquí.</p>
            <Link href="/product" className="mt-6 inline-flex rounded-full bg-foreground px-6 py-3 text-xs font-bold uppercase tracking-[0.08em] text-background transition hover:opacity-90">
              Explorar catálogo
            </Link>
          </div>
        ) : (
          <div className="space-y-3">
            {orders.map((order) => {
              const isExpanded = expandedOrder === order.id;
              const hasDiscount = Number(order.discount_amount) > 0;

              return (
                <article key={order.id} className="overflow-hidden rounded-2xl border border-bd-border bg-surface">
                  <button
                    type="button"
                    onClick={() => toggleOrder(order.id)}
                    aria-expanded={isExpanded}
                    className="w-full px-5 py-5 text-left transition hover:bg-foreground/[0.025] sm:px-6"
                  >
                    <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                      <div>
                        <div className="flex items-center gap-3">
                          <span className={`h-2.5 w-2.5 rounded-full ${order.paid ? "bg-emerald-400" : "bg-amber-300"}`} aria-hidden="true" />
                          <p className="font-semibold text-foreground">Pedido #{order.id}</p>
                        </div>
                        <time className="mt-1 block text-xs text-muted" dateTime={order.created_at}>
                          {new Date(order.created_at).toLocaleDateString("es-PE", {
                            year: "numeric",
                            month: "long",
                            day: "numeric",
                            hour: "2-digit",
                            minute: "2-digit",
                          })}
                        </time>
                      </div>

                      <div className="flex items-center justify-between gap-4 sm:justify-end">
                        <div className="text-right">
                          <p className="font-extrabold tabular-nums text-foreground">S/ {formatMoney(order.total)}</p>
                          {hasDiscount ? <p className="mt-0.5 text-xs text-muted">Descuento S/ {formatMoney(order.discount_amount)}</p> : null}
                        </div>
                        <span className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${
                          order.paid
                            ? "border-success-border bg-success-surface text-success"
                            : "border-warning-border bg-warning-surface text-warning"
                        }`}>
                          {order.paid ? "Pagado" : "Pendiente"}
                        </span>
                        <span className={`text-muted transition-transform ${isExpanded ? "rotate-180" : ""}`} aria-hidden="true">⌄</span>
                      </div>
                    </div>
                  </button>

                  {isExpanded ? (
                    <div className="border-t border-bd-border px-5 pb-5 pt-4 sm:px-6">
                      {order.coupon_code ? (
                        <div className="mb-4 inline-flex rounded-full border border-bd-border px-2.5 py-1 text-xs font-semibold text-muted">
                          Cupón {order.coupon_code} · −S/ {formatMoney(order.discount_amount)}
                        </div>
                      ) : null}

                      <div className="divide-y divide-bd-border">
                        {order.items.map((item) => (
                          <div key={item.id} className="flex items-center justify-between gap-4 py-3">
                            <div className="min-w-0">
                              <Link href={`/product/${item.product.slug}`} className="block truncate text-sm font-medium text-foreground transition hover:underline">
                                {item.product.name}
                              </Link>
                              <p className="mt-1 text-xs text-muted">
                                {item.quantity} × S/ {formatMoney(item.price)}
                              </p>
                            </div>
                            <span className="shrink-0 text-sm font-semibold tabular-nums text-foreground">
                              S/ {formatMoney(Number(item.price) * item.quantity)}
                            </span>
                          </div>
                        ))}
                      </div>

                      {!order.paid ? (
                        <div className="mt-4 rounded-xl border border-warning-border bg-warning-surface p-3 text-xs leading-5 text-warning">
                          Esta orden aún figura como pendiente de pago.
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </article>
              );
            })}
          </div>
        )}

        <div className="mt-8 flex flex-wrap gap-4 border-t border-bd-border pt-6">
          <Link href="/product" className="text-sm font-semibold text-muted transition hover:text-foreground">
            ← Seguir comprando
          </Link>
          {whatsappLink ? (
            <a href={whatsappLink} target="_blank" rel="noopener noreferrer" className="text-sm font-semibold text-muted transition hover:text-foreground">
              Consultar por WhatsApp ↗
            </a>
          ) : null}
        </div>
      </div>
    </div>
  );
}
