"use client";

/**
 * P2 · F1 — the V4 preview's top bar: the shop's name (or its horizontal logo
 * for a light background) back to the home page, the catalogue and the cart.
 *
 * Under `/v4` the V3 header is not drawn (`StorefrontChrome`); this is the
 * least a page needs to be left. The full V4 header is a later phase.
 */

import Link from "next/link";
import { useStorefront } from "../StorefrontProvider";

export function TopBarV4() {
  const { company, branding } = useStorefront();
  const logo = branding.logos?.horizontal_on_light || "";
  const name = company.name || "Tienda";
  return (
    <div className="border-b border-bd-border">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-6 px-4 py-4 sm:px-6 lg:px-10">
        <Link href="/" className="inline-flex min-h-11 items-center font-bold" aria-label={`${name}, inicio`}>
          {logo ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={logo} alt="" className="h-7 w-auto" />
          ) : (
            name
          )}
        </Link>
        <nav aria-label="Tienda" className="flex items-center gap-6 text-sm font-medium">
          <Link href="/product" className="inline-flex min-h-11 items-center hover:underline">Catálogo</Link>
          <Link href="/cart" className="inline-flex min-h-11 items-center hover:underline">Carrito</Link>
        </nav>
      </div>
    </div>
  );
}

export default TopBarV4;
