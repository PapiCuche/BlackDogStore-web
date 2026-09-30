"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { logout, getCurrentUser, isAdminRole, type AuthUser } from "../lib/auth";
import { getSessionKey } from "../lib/cart";
import { apiUrl } from "../lib/api";
import { useStorefront } from "./StorefrontProvider";

const CART_ICON = (
  <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M3 3h2l.4 2M7 13h10l4-8H5.4M7 13l-1.4 7h12.8M17 21a1 1 0 100-2 1 1 0 000 2zM9 21a1 1 0 100-2 1 1 0 000 2z" />
  </svg>
);

const navLinkClass =
  "rounded-lg px-3 py-2 text-sm font-medium text-muted-foreground transition hover:bg-surface hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";

export function Header() {
  const { company, branding, contact } = useStorefront();
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [cartCount, setCartCount] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const userLoggedIn = Boolean(user);

  async function fetchCartCount() {
    try {
      const sessionKey = getSessionKey();
      const res = await fetch(apiUrl("/cart?session_key=" + sessionKey));
      if (!res.ok) return;
      const data = await res.json();
      setCartCount(Array.isArray(data) ? data.length : 0);
    } catch {
      // The header stays usable when the cart badge cannot be refreshed.
    }
  }

  useEffect(() => {
    getCurrentUser().then((u) => setUser(u));

    const handleAuthChange = () => getCurrentUser().then((u) => setUser(u));
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
      setUser(null);
      router.push("/");
      router.refresh();
    });
  }

  const cartLabel = "Carrito" + (cartCount > 0 ? " (" + cartCount + ")" : "");

  return (
    <header className="sticky top-0 z-50 border-b border-bd-border bg-background/95 backdrop-blur-xl">
      <div className="mx-auto flex min-h-16 max-w-7xl items-center justify-between gap-5 px-5 sm:px-6 lg:px-8">
        <Link
          href="/"
          className="group flex min-w-0 shrink-0 items-center gap-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          aria-label={company.name ? "Inicio · " + company.name : "Inicio"}
        >
          {branding.logo_url ? (
            <img
              src={branding.logo_url}
              alt=""
              className="h-8 w-auto max-w-36 object-contain transition-opacity group-hover:opacity-80 sm:h-9"
            />
          ) : null}
          <div className="min-w-0 leading-none">
            <span className="block max-w-44 truncate font-display text-sm font-black uppercase tracking-tight text-foreground sm:max-w-56 sm:text-base">
              {company.name || "Tienda"}
            </span>
            {contact.city ? (
              <span className="mt-1 block max-w-44 truncate text-[9px] font-semibold uppercase tracking-[0.24em] text-muted-foreground sm:max-w-56">
                {contact.city}
              </span>
            ) : null}
          </div>
        </Link>

        <nav className="hidden items-center gap-1 sm:flex" aria-label="Navegación principal">
          <Link href="/product" className={navLinkClass}>
            Catálogo
          </Link>
          <Link href="/services" className={navLinkClass}>
            Servicios
          </Link>
          <Link href="/cart" className={navLinkClass + " relative"}>
            <span className="flex items-center gap-2">
              {CART_ICON}
              <span>Carrito</span>
            </span>
            {cartCount > 0 ? (
              <span className="absolute -right-1 -top-1 flex h-5 min-w-5 items-center justify-center rounded-full bg-primary px-1 text-[9px] font-black text-background">
                {cartCount > 9 ? "9+" : cartCount}
              </span>
            ) : null}
          </Link>

          {userLoggedIn ? (
            <>
              <Link href="/orders" className={navLinkClass}>
                Pedidos
              </Link>
              {isAdminRole(user) ? (
                <Link href="/admin" className={navLinkClass}>
                  Admin
                </Link>
              ) : null}
              <button
                type="button"
                onClick={handleLogout}
                className="ml-2 rounded-lg border border-bd-border bg-surface px-4 py-2 text-xs font-semibold text-muted-foreground transition hover:bg-surface-elevated hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                Salir
              </button>
            </>
          ) : (
            <Link
              href="/auth"
              className="ml-2 rounded-lg bg-primary px-5 py-2.5 text-xs font-bold uppercase tracking-[0.12em] text-background transition hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              Ingresar
            </Link>
          )}
        </nav>

        <div className="flex items-center gap-1 sm:hidden">
          <Link
            href="/cart"
            className="relative rounded-lg p-2.5 text-muted-foreground transition hover:bg-surface hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            aria-label={cartLabel}
          >
            {CART_ICON}
            {cartCount > 0 ? (
              <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-0.5 text-[8px] font-black text-background">
                {cartCount > 9 ? "9+" : cartCount}
              </span>
            ) : null}
          </Link>
          <button
            type="button"
            onClick={() => setMenuOpen((open) => !open)}
            className="rounded-lg p-2.5 text-muted-foreground transition hover:bg-surface hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            aria-label={menuOpen ? "Cerrar menú" : "Abrir menú"}
            aria-expanded={menuOpen}
            aria-controls="mobile-storefront-menu"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
              {menuOpen ? (
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              ) : (
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
              )}
            </svg>
          </button>
        </div>
      </div>

      {menuOpen ? (
        <div id="mobile-storefront-menu" className="border-t border-bd-border bg-background px-5 pb-5 pt-3 sm:hidden">
          <nav className="flex flex-col gap-1" aria-label="Navegación móvil">
            {[
              { href: "/product", label: "Catálogo" },
              { href: "/services", label: "Servicios" },
              { href: "/cart", label: cartLabel },
            ].map((item) => (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => setMenuOpen(false)}
                className="rounded-lg px-3 py-3 text-sm font-medium text-muted-foreground transition hover:bg-surface hover:text-foreground"
              >
                {item.label}
              </Link>
            ))}

            {userLoggedIn ? (
              <>
                <Link
                  href="/orders"
                  onClick={() => setMenuOpen(false)}
                  className="rounded-lg px-3 py-3 text-sm font-medium text-muted-foreground transition hover:bg-surface hover:text-foreground"
                >
                  Mis pedidos
                </Link>
                {isAdminRole(user) ? (
                  <Link
                    href="/admin"
                    onClick={() => setMenuOpen(false)}
                    className="rounded-lg px-3 py-3 text-sm font-medium text-muted-foreground transition hover:bg-surface hover:text-foreground"
                  >
                    Admin
                  </Link>
                ) : null}
                <button
                  type="button"
                  onClick={handleLogout}
                  className="mt-3 rounded-lg border border-bd-border bg-surface px-4 py-3 text-left text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground"
                >
                  Cerrar sesión
                </button>
              </>
            ) : (
              <Link
                href="/auth"
                onClick={() => setMenuOpen(false)}
                className="mt-3 rounded-lg bg-primary px-4 py-3 text-center text-xs font-bold uppercase tracking-[0.12em] text-background"
              >
                Ingresar
              </Link>
            )}
          </nav>
        </div>
      ) : null}
    </header>
  );
}
