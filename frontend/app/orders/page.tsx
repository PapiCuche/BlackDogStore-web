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
  // Phase 3: the tenant's own WhatsApp, not a compiled-in number.
  const whatsappLink = useStorefront().contact.whatsapp_link;
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedOrder, setExpandedOrder] = useState<number | null>(null);
  const router = useRouter();

  useEffect(() => {
    async function load() {
      const user = await getCurrentUser();
      if (!user) {
        router.push("/auth");
        return;
      }
      try {
        const res = await fetchWithAuth(`${API_BASE}/orders/`);
        if (!res.ok) throw new Error("No se pudieron cargar tus pedidos.");
        const data = await res.json();
        setOrders(Array.isArray(data) ? data : []);
        setError(null);
      } catch (err) {
        setOrders([]);
        setError(err instanceof Error ? err.message : "No se pudieron cargar tus pedidos.");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [router]);

  function toggleOrder(id: number) {
    setExpandedOrder((prev) => (prev === id ? null : id));
  }

  return (
    <div className="min-h-screen bg-background px-6 py-12 text-foreground">
      <div className="mx-auto max-w-5xl">
        <div className="mb-8">
          <span className="section-label">Cuenta</span>
          <h1 className="mt-2 font-display text-4xl font-black uppercase tracking-tight text-foreground sm:text-5xl">
            Mis pedidos
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Consulta el importe, el estado de pago y los productos de cada pedido.
          </p>
        </div>

        {error ? (
          <div className="mb-6 rounded-xl border border-red-500/25 bg-red-500/[0.07] p-4 text-sm text-red-300">
            {error}
          </div>
        ) : null}

        {loading ? (
          <div className="space-y-3" aria-busy="true">
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-24 animate-pulse rounded-xl bg-surface" />
            ))}
          </div>
        ) : !error && orders.length === 0 ? (
          <div className="flex flex-col items-center gap-6 rounded-2xl border border-dashed border-bd-border p-10 text-center sm:p-16">
            <div className="flex h-14 w-14 items-center justify-center rounded-xl border border-bd-border bg-surface text-muted-foreground" aria-hidden="true">
              <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.4} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
              </svg>
            </div>
            <div>
              <p className="font-display text-xl font-black uppercase text-foreground">Aún no hay pedidos</p>
              <p className="mt-1 text-sm text-muted-foreground">Las compras asociadas a tu cuenta aparecerán aquí.</p>
            </div>
            <Link
              href="/product"
              className="rounded-xl bg-primary px-6 py-3 text-xs font-black uppercase tracking-[0.12em] text-background transition hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              Explorar catálogo
            </Link>
          </div>
        ) : !error ? (
          <div className="space-y-4">
            {orders.map((order) => {
              const isExpanded = expandedOrder === order.id;
              const hasDiscount = Number(order.discount_amount) > 0;
              return (
                <article key={order.id} className="overflow-hidden rounded-xl border border-bd-border bg-surface">
                  <button
                    type="button"
                    onClick={() => toggleOrder(order.id)}
                    aria-expanded={isExpanded}
                    className="w-full px-5 py-5 text-left transition hover:bg-surface-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent sm:px-6"
                  >
                    <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                      <div className="flex min-w-0 items-center gap-4">
                        <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border ${
                          order.paid
                            ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-300"
                            : "border-amber-400/30 bg-amber-400/10 text-amber-300"
                        }`} aria-hidden="true">
                          {order.paid ? (
                            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                            </svg>
                          ) : (
                            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                          )}
                        </div>
                        <div className="min-w-0">
                          <p className="font-display font-black uppercase text-foreground">Pedido #{order.id}</p>
                          <p className="mt-1 text-xs text-muted-foreground">
                            {new Date(order.created_at).toLocaleDateString("es-PE", {
                              year: "numeric",
                              month: "long",
                              day: "numeric",
                              hour: "2-digit",
                              minute: "2-digit",
                            })}
                          </p>
                        </div>
                      </div>

                      <div className="flex flex-wrap items-center gap-3 sm:justify-end">
                        <div className="mr-2 text-right">
                          <p className="font-display font-black text-foreground">S/ {formatMoney(order.total)}</p>
                          {hasDiscount ? (
                            <p className="text-xs text-muted-foreground">
                              Descuento S/ {formatMoney(order.discount_amount)}
                            </p>
                          ) : null}
                        </div>
                        <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${
                          order.paid
                            ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-300"
                            : "border-amber-400/30 bg-amber-400/10 text-amber-300"
                        }`}>
                          {order.paid ? "Pagado" : "Pendiente"}
                        </span>
                        <svg className={`h-4 w-4 text-muted-foreground transition-transform ${isExpanded ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                        </svg>
                      </div>
                    </div>
                  </button>

                  {isExpanded ? (
                    <div className="border-t border-bd-border bg-background/40 px-5 pb-5 pt-4 sm:px-6">
                      {order.coupon_code ? (
                        <div className="mb-4 inline-flex rounded-lg border border-bd-border bg-surface px-3 py-2 text-xs text-muted-foreground">
                          Cupón {order.coupon_code} · −S/ {formatMoney(order.discount_amount)}
                        </div>
                      ) : null}

                      <div className="space-y-4">
                        {order.items.map((item) => (
                          <div key={item.id} className="flex items-center justify-between gap-4">
                            <div className="min-w-0">
                              <Link
                                href={`/product/${item.product.slug}`}
                                className="line-clamp-1 text-sm font-medium text-foreground transition hover:text-accent"
                              >
                                {item.product.name}
                              </Link>
                              <p className="mt-1 text-xs text-muted-foreground">
                                {item.quantity} × S/ {formatMoney(item.price)}
                              </p>
                            </div>
                            <span className="shrink-0 text-sm font-semibold text-foreground">
                              S/ {formatMoney(Number(item.price) * item.quantity)}
                            </span>
                          </div>
                        ))}
                      </div>

                      {!order.paid ? (
                        <div className="mt-5 rounded-xl border border-amber-400/25 bg-amber-400/[0.07] p-3 text-xs leading-5 text-amber-200">
                          Este pedido todavía figura como pendiente de pago.
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </article>
              );
            })}
          </div>
        ) : null}

        <div className="mt-8 flex flex-wrap gap-x-6 gap-y-3">
          <Link href="/product" className="text-sm text-muted-foreground transition hover:text-foreground">
            ← Seguir comprando
          </Link>
          {whatsappLink ? (
            <a
              href={whatsappLink}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-muted-foreground transition hover:text-foreground"
            >
              Consultar con la tienda →
            </a>
          ) : null}
        </div>
      </div>
    </div>
  );
}
