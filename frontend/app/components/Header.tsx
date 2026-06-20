"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { logout, getCurrentUser } from "../lib/auth";
import { getSessionKey } from "../lib/cart";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000/api";

const CART_ICON = (
  <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M3 3h2l.4 2M7 13h10l4-8H5.4M7 13l-1.4 7h12.8M17 21a1 1 0 100-2 1 1 0 000 2zM9 21a1 1 0 100-2 1 1 0 000 2z" />
  </svg>
);

export function Header() {
  const [userLoggedIn, setUserLoggedIn] = useState(false);
  const [cartCount, setCartCount] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);

  async function fetchCartCount() {
    try {
      const sessionKey = getSessionKey();
      const res = await fetch(`${API_BASE}/cart/?session_key=${sessionKey}`);
      if (!res.ok) return;
      const data = await res.json();
      setCartCount(Array.isArray(data) ? data.length : 0);
    } catch {}
  }

  useEffect(() => {
    getCurrentUser().then((u) => setUserLoggedIn(Boolean(u)));

    const handleAuthChange = () => getCurrentUser().then((u) => setUserLoggedIn(Boolean(u)));
    fetchCartCount();
    window.addEventListener("authChange", handleAuthChange);
    window.addEventListener("cartChange", fetchCartCount);
    return () => {
      window.removeEventListener("authChange", handleAuthChange);
      window.removeEventListener("cartChange", fetchCartCount);
    };
  }, []);

  function handleLogout() {
    logout().finally(() => {
      setUserLoggedIn(false);
      window.location.href = "/";
    });
  }

  return (
    <header className="sticky top-0 z-50 border-b border-white/[0.06] bg-[#080808]/95 backdrop-blur-md">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-5 py-3">

        {/* Logo */}
        <Link href="/" className="group flex items-center gap-3 shrink-0">
          <div className="relative h-10 w-10 shrink-0">
            <img
              src="/assets/branding/logo-icon.png"
              alt="Black Dog Store"
              className="h-full w-full object-contain invert transition-opacity group-hover:opacity-75"
            />
          </div>
          <div className="leading-none">
            <img
              src="/assets/branding/logo-text.png"
              alt="Black Dog Store"
              className="h-5 w-auto object-contain invert opacity-95 transition-opacity group-hover:opacity-70"
            />
            <span className="block text-[9px] font-semibold uppercase tracking-[0.3em] text-zinc-500">
              Apple Specialist
            </span>
          </div>
        </Link>

        {/* Desktop nav */}
        <nav className="hidden items-center gap-0.5 text-sm font-medium text-zinc-400 sm:flex">
          {[
            { href: "/product", label: "Catálogo" },
            { href: "/services", label: "Servicios" },
          ].map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="rounded-lg px-3.5 py-2 transition hover:bg-white/5 hover:text-white"
            >
              {item.label}
            </Link>
          ))}

          {/* Cart */}
          <Link href="/cart" className="relative rounded-lg px-3.5 py-2 transition hover:bg-white/5 hover:text-white">
            <span className="flex items-center gap-1.5">
              {CART_ICON}
              <span>Carrito</span>
            </span>
            {cartCount > 0 && (
              <span className="absolute -right-0.5 -top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-white text-[9px] font-black text-[#080808]">
                {cartCount > 9 ? "9+" : cartCount}
              </span>
            )}
          </Link>

          {/* Auth */}
          {userLoggedIn ? (
            <>
              <Link href="/orders" className="rounded-lg px-3.5 py-2 transition hover:bg-white/5 hover:text-white">
                Pedidos
              </Link>
              <button
                onClick={handleLogout}
                className="ml-2 rounded-full border border-white/10 bg-white/5 px-4 py-2 text-xs font-semibold text-zinc-400 transition hover:border-white/25 hover:text-white"
              >
                Salir
              </button>
            </>
          ) : (
            <Link
              href="/auth"
              className="ml-3 rounded-full bg-white px-5 py-2 text-xs font-bold uppercase tracking-wider text-[#080808] transition hover:bg-zinc-200"
            >
              Ingresar
            </Link>
          )}
        </nav>

        {/* Mobile: cart + hamburger */}
        <div className="flex items-center gap-2 sm:hidden">
          <Link href="/cart" className="relative rounded-lg p-2 text-zinc-400 transition hover:text-white">
            {CART_ICON}
            {cartCount > 0 && (
              <span className="absolute -right-0.5 -top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-white text-[9px] font-black text-[#080808]">
                {cartCount > 9 ? "9+" : cartCount}
              </span>
            )}
          </Link>
          <button
            onClick={() => setMenuOpen(!menuOpen)}
            className="rounded-lg p-2 text-zinc-400 transition hover:bg-white/5 hover:text-white"
            aria-label="Abrir menú"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              {menuOpen
                ? <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                : <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
              }
            </svg>
          </button>
        </div>
      </div>

      {/* Mobile menu */}
      {menuOpen && (
        <div className="border-t border-white/[0.06] bg-[#080808] px-5 pb-5 pt-3 sm:hidden">
          <nav className="flex flex-col gap-1 text-sm font-medium">
            {[
              { href: "/product", label: "Catálogo" },
              { href: "/services", label: "Servicios" },
              { href: "/cart", label: `Carrito${cartCount > 0 ? ` (${cartCount})` : ""}` },
            ].map((item) => (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => setMenuOpen(false)}
                className="rounded-lg px-3 py-2.5 text-zinc-400 transition hover:bg-white/5 hover:text-white"
              >
                {item.label}
              </Link>
            ))}
            {userLoggedIn ? (
              <>
                <Link
                  href="/orders"
                  onClick={() => setMenuOpen(false)}
                  className="rounded-lg px-3 py-2.5 text-zinc-400 hover:bg-white/5 hover:text-white"
                >
                  Mis pedidos
                </Link>
                <button
                  onClick={handleLogout}
                  className="mt-2 w-full rounded-full border border-white/10 py-2.5 text-xs font-semibold text-zinc-400"
                >
                  Cerrar sesión
                </button>
              </>
            ) : (
              <Link
                href="/auth"
                onClick={() => setMenuOpen(false)}
                className="mt-3 block rounded-full bg-white px-4 py-2.5 text-center text-xs font-bold uppercase tracking-wider text-[#080808]"
              >
                Ingresar
              </Link>
            )}
          </nav>
        </div>
      )}
    </header>
  );
}
