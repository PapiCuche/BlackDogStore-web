"use client";

/**
 * P2 · F1 — the V4 preview's top bar. P2 · F2 — a small V4 header.
 *
 * Under `/v4` the V3 header is not drawn (`StorefrontChrome`); this is it.
 *
 * THE LOGO IS THE SHOP'S, chosen by the same rule as everywhere else
 * (`pickLogo`, through `headerLogos`), on a LIGHT surface: the V4 is light always, so a variant for a
 * dark background is never drawn. On desktop the horizontal lockup, at the
 * 220 px its manual sets as the minimum; on a phone, where it cannot fit at
 * that width next to the links, the isotype (the piece designed for that
 * size). No logo for a light surface: the shop's name, never an illegible one.
 *
 * THE LINKS: Catálogo (the V4 home), Servicio only when the shop published
 * services (the footer's rule: a shop that does not repair does not say it
 * does) and Carrito. Plain links in the reading order, with the V4's black
 * focus ring (`v4.css`, ≥ 3:1) and no motion of their own.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useStorefront } from "../StorefrontProvider";
import { headerLogos } from "../../lib/brand-logo";

const HOME = "/v4";

export function TopBarV4() {
  const { company, branding, services } = useStorefront();
  const pathname = usePathname();
  const name = company.name || "Tienda";
  // The V4 is light always: the logos for a light surface, whatever the visitor's theme.
  const { wide: desktop, compact: mobile } = headerLogos(branding, "light");
  const onHome = pathname === HOME || pathname === `${HOME}/`;

  return (
    <header className="v4-header">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 sm:px-6 lg:px-10 lg:py-4">
        <Link href={HOME} className="inline-flex min-h-11 shrink-0 items-center font-bold" aria-label={`${name}, inicio`}>
          {desktop || mobile ? (
            <>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img data-logo="mobile" src={mobile || desktop} alt="" className="h-9 w-auto object-contain lg:hidden" />
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                data-logo="desktop"
                src={desktop || mobile}
                alt=""
                className="hidden h-auto max-h-24 w-[240px] object-contain object-left lg:block"
              />
            </>
          ) : (
            name
          )}
        </Link>
        <nav aria-label="Tienda">
          <ul className="flex items-center gap-5 text-sm font-medium sm:gap-8">
            <li>
              <Link href={HOME} className="v4-nav-link" aria-current={onHome ? "page" : undefined}>Catálogo</Link>
            </li>
            {services.length > 0 ? (
              <li><Link href="/services" className="v4-nav-link">Servicio</Link></li>
            ) : null}
            <li><Link href="/cart" className="v4-nav-link">Carrito</Link></li>
          </ul>
        </nav>
      </div>
    </header>
  );
}

export default TopBarV4;
