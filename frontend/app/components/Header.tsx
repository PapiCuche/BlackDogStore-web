"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { logout, getCurrentUser, isAdminRole, type AuthUser } from "../lib/auth";
import { BrandLogo } from "./BrandLogo";
import { ThemeToggle } from "./ThemeToggle";
import { useTheme } from "./ThemeProvider";
import { getSessionKey } from "../lib/cart";
import { apiUrl } from "../lib/api";
import { useStorefront } from "./StorefrontProvider";

const CART_ICON = (
  <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M3 3h2l.4 2M7 13h10l4-8H5.4M7 13l-1.4 7h12.8M17 21a1 1 0 100-2 1 1 0 000 2zM9 21a1 1 0 100-2 1 1 0 000 2z" />
  </svg>
);

const CATEGORY_LINKS = [
  { href: "/product?category=iphone", label: "iPhone" },
  { href: "/product?category=apple-watch", label: "Watch" },
  { href: "/product?category=ipad", label: "iPad" },
  { href: "/product?category=mac", label: "Mac" },
  { href: "/product?category=accesorios", label: "Accesorios" },
  { href: "/product?category=audifonos", label: "Audífonos" },
];

export function Header() {
  // Phase 3: the shop's own name and logo, from the tenant that owns this host.
  const { company } = useStorefront();
  // La cabecera usa `bg-background`, así que su superficie ES el tema.
  const { resolved } = useTheme();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [cartCount, setCartCount] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const [catalogOpen, setCatalogOpen] = useState(false);
  const userLoggedIn = Boolean(user);

  async function fetchCartCount() {
    try {
      const sessionKey = getSessionKey();
      const res = await fetch(apiUrl(`/cart?session_key=${sessionKey}`));
      if (!res.ok) return;
      const data = await res.json();
      setCartCount(Array.isArray(data) ? data.length : 0);
    } catch {}
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
      window.location.href = "/";
    });
  }

  return (
    <header className="sticky top-0 z-50 border-b border-bd-border bg-background/95 backdrop-blur-md">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-5 py-3">

        {/*
          M12E — el logotipo LO ELIGE `BrandLogo`, no esta cabecera.

          Antes se dibujaba `branding.logo_url` tal cual, y eso producía el
          defecto que esta fase cierra: un logotipo negro sobre una cabecera
          negra es invisible, y no se arregla haciéndolo más grande.

          La superficie se deriva del tema resuelto porque la cabecera usa
          `bg-background`: en oscuro es oscura, en claro es clara. Cuando una
          superficie NO sigue al tema —un hero negro dentro de un tema claro—
          se le pasa la suya, que es para lo que existe el parámetro.

          El nombre en tipografía sólo aparece si NO hay logo. El lockup ya
          contiene «BLACK DOG STORE»: dibujarlo otra vez al lado duplica la
          marca.
        */}
        <Link
          href="/"
          className="group flex items-center gap-3 pr-2 shrink-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-primary"
          aria-label={company.name}
        >
          <BrandLogo
            placement="header"
            surface={resolved}
            /* El horizontal del piloto es 1040x352: a 40px de alto da 118px de
               ancho, por debajo de los 220px que su manual exige. A 56/64 da
               165/189... sigue corto en móvil, y por eso el móvil usa una
               cabecera más baja donde la variante compacta es aceptable.
               A partir de `lg` se respeta el mínimo. */
            className="h-10 w-auto object-contain transition-opacity group-hover:opacity-75 sm:h-12 lg:h-[76px]"
            wordmarkClassName="font-display text-base font-black uppercase tracking-tight text-foreground"
          />
        </Link>

        {/* Desktop nav */}
        <nav className="hidden items-center gap-0.5 text-sm font-medium text-muted lg:flex">

          {/* Catalog with dropdown */}
          <div
            className="relative"
            onMouseEnter={() => setCatalogOpen(true)}
            onMouseLeave={() => setCatalogOpen(false)}
          >
            <Link
              href="/product"
              className="flex items-center gap-1 rounded-lg px-3.5 py-2 transition hover:bg-surface-2 hover:text-foreground"
            >
              Catálogo
              <svg className="h-3 w-3 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M19 9l-7 7-7-7" />
              </svg>
            </Link>

            {catalogOpen && (
              <div className="absolute left-0 top-full z-50 mt-1 w-52 overflow-hidden rounded-2xl border border-bd-border bg-surface py-2 shadow-2xl">
                <div className="px-3 pb-2 pt-1">
                  <p className="text-[9px] font-bold uppercase tracking-[0.25em] text-muted">Categorías</p>
                </div>
                {CATEGORY_LINKS.map((item) => (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={() => setCatalogOpen(false)}
                    className="block px-4 py-2 text-sm text-muted transition hover:bg-surface hover:text-foreground"
                  >
                    {item.label}
                  </Link>
                ))}
                <div className="mt-1 border-t border-bd-border px-4 pt-2 pb-1">
                  <Link
                    href="/product"
                    onClick={() => setCatalogOpen(false)}
                    className="text-xs font-bold uppercase tracking-widest text-muted transition hover:text-foreground"
                  >
                    Ver todo →
                  </Link>
                </div>
              </div>
            )}
          </div>

          <Link href="/services" className="rounded-lg px-3.5 py-2 transition hover:bg-surface-2 hover:text-foreground">
            Servicios
          </Link>

          {/* Cart */}
          <Link href="/cart" className="relative rounded-lg px-3.5 py-2 transition hover:bg-surface-2 hover:text-foreground">
            <span className="flex items-center gap-1.5">
              {CART_ICON}
              <span>Carrito</span>
            </span>
            {cartCount > 0 && (
              <span className="absolute -right-0.5 -top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-primary text-[9px] font-black text-background">
                {cartCount > 9 ? "9+" : cartCount}
              </span>
            )}
          </Link>

          {/* Auth */}
          {userLoggedIn ? (
            <>
              <Link href="/orders" className="rounded-lg px-3.5 py-2 transition hover:bg-surface-2 hover:text-foreground">
                Pedidos
              </Link>
              {isAdminRole(user) && (
                <Link href="/admin" className="rounded-lg px-3.5 py-2 transition hover:bg-surface-2 hover:text-foreground">
                  Admin
                </Link>
              )}
              <button
                onClick={handleLogout}
                className="ml-2 rounded-full border border-bd-border bg-surface-2 px-4 py-2 text-xs font-semibold text-muted transition hover:border-foreground/25 hover:text-foreground"
              >
                Salir
              </button>
            </>
          ) : (
            <Link
              href="/auth"
              className="ml-3 rounded-full bg-foreground px-5 py-2 text-xs font-bold uppercase tracking-wider text-background transition hover:bg-foreground/90"
            >
              Ingresar
            </Link>
          )}
          <ThemeToggle className="ml-1" />
        </nav>

        {/* Móvil/tablet: sólo lo esencial — carrito, tema, menú. */}
        <div className="flex items-center gap-1.5 lg:hidden">
          <ThemeToggle />
          <Link href="/cart" className="relative rounded-lg p-2 text-muted transition hover:text-foreground">
            {CART_ICON}
            {cartCount > 0 && (
              <span className="absolute -right-0.5 -top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-primary text-[9px] font-black text-background">
                {cartCount > 9 ? "9+" : cartCount}
              </span>
            )}
          </Link>
          <button
            onClick={() => setMenuOpen(!menuOpen)}
            className="rounded-lg p-2 text-muted transition hover:bg-surface-2 hover:text-foreground"
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
        <div className="border-t border-bd-border bg-background px-5 pb-5 pt-3 lg:hidden">
          <nav className="flex flex-col gap-1 text-sm font-medium">
            <p className="px-3 pt-1 pb-1 text-[9px] font-bold uppercase tracking-[0.25em] text-muted">Categorías</p>
            {CATEGORY_LINKS.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => setMenuOpen(false)}
                className="rounded-lg px-3 py-2 text-muted transition hover:bg-surface-2 hover:text-foreground"
              >
                {item.label}
              </Link>
            ))}
            <Link
              href="/product"
              onClick={() => setMenuOpen(false)}
              className="rounded-lg px-3 py-2 text-muted transition hover:bg-surface-2 hover:text-foreground"
            >
              Todo el catálogo →
            </Link>

            <div className="my-2 border-t border-bd-border" />

            {[
              { href: "/services", label: "Servicios" },
              { href: "/cart", label: `Carrito${cartCount > 0 ? ` (${cartCount})` : ""}` },
            ].map((item) => (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => setMenuOpen(false)}
                className="rounded-lg px-3 py-2.5 text-muted transition hover:bg-surface-2 hover:text-foreground"
              >
                {item.label}
              </Link>
            ))}
            {userLoggedIn ? (
              <>
                <Link
                  href="/orders"
                  onClick={() => setMenuOpen(false)}
                  className="rounded-lg px-3 py-2.5 text-muted hover:bg-surface-2 hover:text-foreground"
                >
                  Mis pedidos
                </Link>
                {isAdminRole(user) && (
                  <Link
                    href="/admin"
                    onClick={() => setMenuOpen(false)}
                    className="rounded-lg px-3 py-2.5 text-muted hover:bg-surface-2 hover:text-foreground"
                  >
                    Admin
                  </Link>
                )}
                <button
                  onClick={handleLogout}
                  className="mt-2 w-full rounded-full border border-bd-border py-2.5 text-xs font-semibold text-muted"
                >
                  Cerrar sesión
                </button>
              </>
            ) : (
              <Link
                href="/auth"
                onClick={() => setMenuOpen(false)}
                className="mt-3 block rounded-full bg-foreground px-4 py-2.5 text-center text-xs font-bold uppercase tracking-wider text-background"
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
