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
        const data = await res.json();
        setOrders(Array.isArray(data) ? data : []);
      } catch {
        setOrders([]);
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
    <div className="min-h-screen bg-zinc-950 px-6 py-12">
      <div className="mx-auto max-w-4xl">
        <div className="mb-8">
          <p className="text-sm uppercase tracking-[0.3em] font-semibold text-emerald-400/60">Cuenta</p>
          <h1 className="mt-1 text-4xl font-bold text-white">Mis pedidos</h1>
        </div>

        {loading ? (
          <div className="space-y-3">
            {[1, 2, 3].map((i) => <div key={i} className="h-20 animate-pulse rounded-2xl bg-white/5" />)}
          </div>
        ) : orders.length === 0 ? (
          <div className="flex flex-col items-center gap-6 rounded-3xl border border-dashed border-white/10 p-16 text-center">
            <svg className="h-16 w-16 text-slate-700" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
            </svg>
            <div>
              <p className="text-lg font-semibold text-white">No tienes pedidos aún</p>
              <p className="mt-1 text-sm text-slate-400">Tus compras aparecerán aquí.</p>
            </div>
            <Link href="/product" className="rounded-full bg-emerald-500 px-6 py-3 text-sm font-semibold text-white transition hover:bg-emerald-600">
              Explorar catálogo
            </Link>
          </div>
        ) : (
          <div className="space-y-4">
            {orders.map((order) => {
              const isExpanded = expandedOrder === order.id;
              const hasDiscount = Number(order.discount_amount) > 0;
              return (
                <div key={order.id} className="rounded-2xl border border-white/10 bg-white/5 overflow-hidden">
                  <button
                    onClick={() => toggleOrder(order.id)}
                    className="w-full text-left px-6 py-5 transition hover:bg-white/5"
                  >
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                      <div className="flex items-center gap-4">
                        <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full border ${order.paid ? "border-emerald-500/40 bg-emerald-500/10" : "border-slate-600 bg-slate-800"}`}>
                          {order.paid ? (
                            <svg className="h-5 w-5 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                            </svg>
                          ) : (
                            <svg className="h-5 w-5 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                          )}
                        </div>
                        <div>
                          <p className="font-semibold text-white">Pedido #{order.id}</p>
                          <p className="text-xs text-slate-500">
                            {new Date(order.created_at).toLocaleDateString("es-PE", { year: "numeric", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                          </p>
                        </div>
                      </div>

                      <div className="flex items-center gap-6">
                        <div className="text-right">
                          <p className="font-bold text-white">S/ {formatMoney(order.total)}</p>
                          {hasDiscount && (
                            <p className="text-xs text-emerald-400">Ahorraste S/ {formatMoney(order.discount_amount)}</p>
                          )}
                        </div>
                        <span className={`rounded-full px-3 py-1 text-xs font-semibold ${order.paid ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30" : "bg-yellow-500/20 text-yellow-400 border border-yellow-500/30"}`}>
                          {order.paid ? "Pagado" : "Pendiente"}
                        </span>
                        <svg className={`h-4 w-4 text-slate-400 transition-transform ${isExpanded ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                        </svg>
                      </div>
                    </div>
                  </button>

                  {isExpanded && (
                    <div className="border-t border-white/5 px-6 pb-5 pt-4">
                      {order.coupon_code && (
                        <div className="mb-4 inline-flex items-center gap-2 rounded-full bg-emerald-500/10 border border-emerald-500/20 px-3 py-1 text-xs font-medium text-emerald-400">
                          🏷️ Cupón: {order.coupon_code} (−{formatMoney(order.discount_amount)} S/)
                        </div>
                      )}
                      <div className="space-y-3">
                        {order.items.map((item) => (
                          <div key={item.id} className="flex items-center justify-between gap-4">
                            <div className="flex items-center gap-3">
                              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-white/10 bg-white/5 text-slate-600">
                                <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                                </svg>
                              </div>
                              <div>
                                <Link href={`/product/${item.product.slug}`} className="text-sm font-medium text-white hover:text-emerald-400 transition">
                                  {item.product.name}
                                </Link>
                                <p className="text-xs text-slate-500">Cant.: {item.quantity} × S/ {formatMoney(item.price)}</p>
                              </div>
                            </div>
                            <span className="text-sm font-semibold text-white">S/ {formatMoney(Number(item.price) * item.quantity)}</span>
                          </div>
                        ))}
                      </div>

                      {!order.paid && (
                        <div className="mt-4 rounded-xl border border-yellow-500/20 bg-yellow-500/10 p-3 text-xs text-yellow-300">
                          Este pedido está pendiente de pago. Si tienes dudas, contáctanos por WhatsApp.
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        <div className="mt-8 flex gap-4">
          <Link href="/product" className="text-sm text-slate-400 hover:text-white transition">← Seguir comprando</Link>
          <a href={whatsappLink || "#"} className="text-sm text-emerald-400 hover:text-emerald-300 transition">¿Problemas con tu pedido? WhatsApp →</a>
        </div>
      </div>
    </div>
  );
}
