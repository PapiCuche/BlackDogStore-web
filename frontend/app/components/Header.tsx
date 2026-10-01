"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { logout, getCurrentUser, isAdminRole, type AuthUser } from "../lib/auth";
import { getSessionKey } from "../lib/cart";
import { apiUrl } from "../lib/api";
import { useStorefront } from "./StorefrontProvider";

const CART_ICON = (
  <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M3 3h2l.4 2M7 13h10l4-8H5.4M7 13l-1.4 7h12.8M17 21a1 1 0 100-2 1 1 0 000 2zM9 21a1 1 0 100-2 1 1 0 000 2z" />
  </svg>
);

const MOBILE_LINKS = [
  { href: "/product", label: "Catálogo" },
  { href: "/services", label: "Servicios" },
];

export function Header() {
  const { company, branding, contact } = useStorefront();
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [cartCount, setCartCount] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const userLoggedIn = Boolean(user);

  function isActive(href: string) {
    if (href === "/admin") return pathname.startsWith("/admin");
    if (href === "/product") return pathname.startsWith("/product");
    return pathname === href || pathname.startsWith(`${href}/`);
  }

  function navLinkClass(href: string) {
    return `rounded-lg px-3 py-2 text-sm font-medium transition ${
      isActive(href)
        ? "bg-foreground/[0.08] text-foreground"
        : "text-muted hover:bg-foreground/[0.05] hover:text-foreground"
    }`;
  }

  async function fetchCartCount() {
    try {
      const sessionKey = getSessionKey();
      const res = await fetch(apiUrl(`/cart?session_key=${sessionKey}`));
      if (!res.ok) return;
      const data = await res.json();
      if (!Array.isArray(data)) return;
      const count = data.reduce((sum: number, item: { quantity?: number }) => {
        const quantity = Number(item?.quantity ?? 1);
        return sum + (Number.isFinite(quantity) && quantity > 0 ? quantity : 1);
      }, 0);
      setCartCount(count);
    } catch {
      // The navigation stays usable when cart count cannot be refreshed.
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

  useEffect(() => {
    if (!menuOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [menuOpen]);

  function handleLogout() {
    logout().finally(() => {
      setUser(null);
      setMenuOpen(false);
      router.replace("/");
      router.refresh();
    });
  }

  return (
    <header className="sticky top-0 z-50 border-b border-bd-border/80 bg-background/95 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between gap-4 px-5 lg:px-8">
        <Link
          href="/"
          className="group flex min-w-0 flex-1 items-center gap-2 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70 sm:gap-3 md:flex-none"
          aria-label={company.name ? `Ir al inicio de ${company.name}` : "Ir al inicio"}
        >
          {branding.logo_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={branding.logo_url}
              alt=""
              className="h-8 w-auto max-w-24 shrink-0 object-contain transition-opacity group-hover:opacity-80 sm:max-w-36 lg:max-w-44"
            />
          ) : null}
          <span className="min-w-0 leading-none">
            <span className="block truncate font-display text-sm font-extrabold uppercase tracking-[-0.02em] text-foreground sm:text-base">
              {company.name || "Tienda"}
            </span>
            {contact.city ? (
              <span className="mt-1 block truncate text-[9px] font-semibold uppercase tracking-[0.22em] text-muted">
                {contact.city}
              </span>
            ) : null}
          </span>
        </Link>

        <nav className="hidden items-center gap-1 md:flex" aria-label="Navegación principal">
          <Link
            href="/product"
            aria-current={isActive("/product") ? "page" : undefined}
            className={navLinkClass("/product")}
          >
            Catálogo
          </Link>
          <Link
            href="/services"
            aria-current={isActive("/services") ? "page" : undefined}
            className={navLinkClass("/services")}
          >
            Servicios
          </Link>
          {userLoggedIn ? (
            <Link
              href="/orders"
              aria-current={isActive("/orders") ? "page" : undefined}
              className={navLinkClass("/orders")}
            >
              Pedidos
            </Link>
          ) : null}
          {user && isAdminRole(user) ? (
            <Link
              href="/admin"
              aria-current={isActive("/admin") ? "page" : undefined}
              className={navLinkClass("/admin")}
            >
              Admin
            </Link>
          ) : null}
        </nav>

        <div className="flex shrink-0 items-center gap-1.5">
          <Link
            href="/cart"
            aria-label={cartCount ? `Carrito, ${cartCount} unidades` : "Carrito"}
            className="relative flex h-11 w-11 items-center justify-center rounded-lg text-muted transition hover:bg-foreground/[0.05] hover:text-foreground"
          >
            {CART_ICON}
            {cartCount > 0 ? (
              <span className="absolute right-0 top-0 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[9px] font-extrabold text-background">
                {cartCount > 99 ? "99+" : cartCount}
              </span>
            ) : null}
          </Link>

          {userLoggedIn ? (
            <button
              type="button"
              onClick={handleLogout}
              className="hidden rounded-lg border border-bd-border px-3.5 py-2 text-xs font-semibold text-muted transition hover:border-foreground/25 hover:text-foreground md:inline-flex"
            >
              Salir
            </button>
          ) : (
            <Link
              href="/auth"
              className="hidden rounded-lg bg-primary px-4 py-2.5 text-xs font-bold uppercase tracking-[0.08em] text-background transition hover:opacity-90 md:inline-flex"
            >
              Ingresar
            </Link>
          )}

          <button
            type="button"
            onClick={() => setMenuOpen((open) => !open)}
            className="flex h-11 w-11 items-center justify-center rounded-lg text-muted transition hover:bg-foreground/[0.05] hover:text-foreground md:hidden"
            aria-label={menuOpen ? "Cerrar menú" : "Abrir menú"}
            aria-expanded={menuOpen}
            aria-controls="mobile-navigation"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
              {menuOpen ? (
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              ) : (
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 7h16M4 12h16M4 17h16" />
              )}
            </svg>
          </button>
        </div>
      </div>

      {menuOpen ? (
        <div id="mobile-navigation" className="border-t border-bd-border bg-background px-5 py-4 md:hidden">
          <nav className="mx-auto flex max-w-7xl flex-col gap-1" aria-label="Navegación móvil">
            {MOBILE_LINKS.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => setMenuOpen(false)}
                aria-current={isActive(item.href) ? "page" : undefined}
                className={`${navLinkClass(item.href)} py-3`}
              >
                {item.label}
              </Link>
            ))}
            {userLoggedIn ? (
              <Link
                href="/orders"
                onClick={() => setMenuOpen(false)}
                aria-current={isActive("/orders") ? "page" : undefined}
                className={`${navLinkClass("/orders")} py-3`}
              >
                Mis pedidos
              </Link>
            ) : null}
            {user && isAdminRole(user) ? (
              <Link
                href="/admin"
                onClick={() => setMenuOpen(false)}
                aria-current={isActive("/admin") ? "page" : undefined}
                className={`${navLinkClass("/admin")} py-3`}
              >
                Admin
              </Link>
            ) : null}
            <div className="my-2 border-t border-bd-border" />
            {userLoggedIn ? (
              <button type="button" onClick={handleLogout} className="rounded-lg border border-bd-border px-4 py-3 text-left text-sm font-medium text-muted">
                Cerrar sesión
              </button>
            ) : (
              <Link href="/auth" onClick={() => setMenuOpen(false)} className="rounded-lg bg-primary px-4 py-3 text-center text-xs font-bold uppercase tracking-[0.08em] text-background">
                Ingresar
              </Link>
            )}
          </nav>
        </div>
      ) : null}
    </header>
  );
}
