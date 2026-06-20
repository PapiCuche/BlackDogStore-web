"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getSessionKey } from "../lib/cart";
import { API_BASE } from "../lib/api";
import { fetchWithAuth, getCurrentUser } from "../lib/auth";

type Coupon = { code: string; discount_percent: number };

export default function CheckoutPage() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [cancelled, setCancelled] = useState(false);
  const [coupon, setCoupon] = useState<Coupon | null>(null);
  const sessionKey = getSessionKey();

  useEffect(() => {
    async function loadData() {
      if (typeof window === "undefined") return;
      const params = new URLSearchParams(window.location.search);
      setCancelled(params.get("cancelled") === "true");

      // Restore coupon
      const saved = sessionStorage.getItem("blackdog_coupon");
      if (saved) setCoupon(JSON.parse(saved));

      // Pre-fill from auth
      try {
        const profile = await getCurrentUser();
        if (profile) {
          if (profile.email) setEmail(profile.email);
          if (profile.first_name) setName(profile.first_name);
        }
      } catch {}
    }
    loadData();
  }, []);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setLoading(true);
    setMessage(null);
    try {
      const body: Record<string, string> = {
        session_key: sessionKey,
        customer_name: name,
        customer_email: email,
      };
      if (coupon) body.coupon_code = coupon.code;

      const res = await fetchWithAuth(`${API_BASE}/payments/create-checkout-session/`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => null);
        throw new Error(err?.detail || "No se pudo crear la sesión de pago.");
      }
      const data = await res.json();
      if (data.url) {
        sessionStorage.removeItem("blackdog_coupon");
        window.location.href = data.url;
      }
    } catch (e: unknown) {
      setMessage(e instanceof Error ? e.message : "Error al iniciar el pago.");
      setLoading(false);
    }
  }

  const inputClass = "mt-2 w-full rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-white placeholder-slate-500 focus:border-emerald-500/50 focus:outline-none";
  const labelClass = "block text-sm font-medium text-slate-300";

  return (
    <div className="min-h-screen bg-zinc-950 px-6 py-12">
      <div className="mx-auto max-w-lg">
        <div className="mb-8">
          <p className="text-sm uppercase tracking-[0.3em] font-semibold text-emerald-400/60">Pago seguro</p>
          <h1 className="mt-1 text-4xl font-bold text-white">Checkout</h1>
        </div>

        {cancelled && (
          <div className="mb-5 rounded-2xl border border-yellow-500/20 bg-yellow-500/10 p-4 text-sm text-yellow-300">
            Pago cancelado. Tu carrito sigue disponible.
          </div>
        )}
        {message && (
          <div className="mb-5 rounded-2xl border border-red-500/20 bg-red-500/10 p-4 text-sm text-red-300">{message}</div>
        )}

        <div className="rounded-2xl border border-white/10 bg-white/5 p-8">
          {coupon && (
            <div className="mb-5 flex items-center justify-between rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3">
              <div>
                <span className="text-xs text-slate-400">Cupón aplicado:</span>
                <div className="text-sm font-bold text-emerald-400">{coupon.code} — {coupon.discount_percent}% de descuento</div>
              </div>
              <Link href="/cart" className="text-xs text-slate-500 hover:text-slate-300">Cambiar</Link>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-5">
            <div>
              <label className={labelClass}>Nombre completo</label>
              <input value={name} onChange={(e) => setName(e.target.value)} className={inputClass} placeholder="Juan Pérez" required />
            </div>
            <div>
              <label className={labelClass}>Correo electrónico</label>
              <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} className={inputClass} placeholder="juan@ejemplo.com" required />
            </div>

            <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-4 text-sm text-emerald-300">
              <span className="flex items-center gap-2">
                <svg className="h-4 w-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                </svg>
                Pago procesado por Stripe. Tus datos están protegidos con SSL.
              </span>
            </div>

            <button
              className="w-full rounded-full bg-emerald-500 px-6 py-3.5 text-sm font-semibold text-white shadow-lg transition hover:bg-emerald-600 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={loading}
            >
              {loading ? "Redirigiendo a Stripe..." : "Pagar con tarjeta →"}
            </button>
          </form>

          <div className="mt-6 text-center">
            <Link href="/cart" className="text-sm text-slate-500 hover:text-slate-300 transition">
              ← Volver al carrito
            </Link>
          </div>
        </div>

        <div className="mt-4 flex justify-center gap-6 text-xs text-slate-600">
          <span>🔒 SSL Encriptado</span>
          <span>💳 Stripe Payments</span>
          <span>✓ Compra protegida</span>
        </div>
      </div>
    </div>
  );
}
