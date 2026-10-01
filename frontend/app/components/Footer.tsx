"use client";

/**
 * Storefront footer — tenant-aware from Phase 3.
 *
 * Name, logo, address, phone, WhatsApp and social links now come from the
 * company that owns this host, via `useStorefront()`. What is left hardcoded is
 * genuinely generic (navigation labels, section headings); the identity is not.
 *
 * Every block DISAPPEARS when the tenant has not configured it. A company with
 * no address shows no address line — it does not show somebody else's, and it
 * does not show an empty icon with a blank next to it.
 */

import Link from "next/link";
import { useStorefront } from "./StorefrontProvider";
import { BrandLogo } from "./BrandLogo";

const WHATSAPP_SVG = (
  <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 24 24">
    <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z" />
  </svg>
);

export function Footer() {
  const { company, contact, services } = useStorefront();
  const storeName = company.name;

  return (
    <footer className="relative border-t border-bd-border bg-background">
      {/* Top CTA band */}
      <div className="relative overflow-hidden bg-inverse px-6 py-12 text-center topo-bg">
        <div className="relative z-10 mx-auto max-w-2xl">
          {/*
            Esta banda se pinta con `bg-foreground`, que tras la traducción de paleta
            de M12F ES el color del texto: su contraste es el CONTRARIO al de la
            página. En tema oscuro sale clara; en tema claro, oscura. Por eso
            `surface="inverse"` y no `"theme"`.

            Antes leía `branding.logo_url` directamente y se saltaba las seis
            variantes por contraste: sobre esta banda invertida, eso ponía el
            logotipo del contraste equivocado exactamente la mitad del tiempo.
          */}
          <div className="mb-6 flex justify-center">
            <BrandLogo
              placement="hero"
              surface="inverse"
              className="h-28 w-auto object-contain"
              wordmarkClassName="font-display text-3xl font-black uppercase tracking-tight text-inverse-foreground"
            />
          </div>
          <p className="font-display text-4xl font-black uppercase tracking-tight text-inverse-foreground sm:text-5xl">
            ¿Necesitas ayuda?
          </p>
          <p className="mt-3 text-sm text-inverse-muted">
            Escríbenos y te respondemos lo antes posible.
          </p>
          {contact.whatsapp_link ? (
            <a
              href={contact.whatsapp_link}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-6 inline-flex min-h-11 items-center gap-2.5 rounded-full bg-inverse-foreground px-8 py-3.5 text-sm font-bold uppercase tracking-widest text-inverse transition-opacity hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-inverse-foreground"
            >
              {WHATSAPP_SVG}
              Escribir al WhatsApp
            </a>
          ) : contact.email ? (
            <a
              href={`mailto:${contact.email}`}
              className="mt-6 inline-flex min-h-11 items-center gap-2.5 rounded-full bg-inverse-foreground px-8 py-3.5 text-sm font-bold uppercase tracking-widest text-inverse transition-opacity hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-inverse-foreground"
            >
              Escríbenos
            </a>
          ) : null}
        </div>
      </div>

      {/* Main footer */}
      <div className="mx-auto max-w-7xl px-6 py-14 lg:px-8">
        <div className="grid gap-10 sm:grid-cols-2 lg:grid-cols-4">

          {/* Brand */}
          <div className="lg:col-span-2">
            <div className="flex items-center gap-4">
              {/*
                Sobre `bg-background`, que sigue al tema.

                El nombre YA NO se escribe al lado: el lockup lo contiene, y
                repetirlo junto a él duplica la marca dentro de su propia área de
                protección. `BrandLogo` sólo escribe el nombre cuando NO hay
                logotipo, que es cuando el nombre ES la identidad. La ciudad se
                queda: es otra información, no la marca otra vez.
              */}
              <BrandLogo
                placement="header"
                surface="theme"
                className="h-11 w-auto object-contain"
                wordmarkClassName="font-display text-lg font-black uppercase tracking-tight text-foreground"
              />
              <div>
                {contact.city ? (
                  <p className="mt-1 text-[10px] uppercase tracking-[0.25em] text-muted">
                    {contact.city}
                  </p>
                ) : null}
              </div>
            </div>

            {/* Contact info */}
            <div className="mt-6 space-y-3">
              {contact.whatsapp_link ? (
                <a
                  href={contact.whatsapp_link}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-2.5 text-sm text-muted transition hover:text-foreground"
                >
                  {WHATSAPP_SVG}
                  {contact.phone || contact.whatsapp_number}
                </a>
              ) : contact.phone ? (
                <p className="text-sm text-muted">{contact.phone}</p>
              ) : null}
              {contact.address ? (
                <div className="flex items-start gap-2.5 text-sm text-muted">
                  <svg className="mt-0.5 h-4 w-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
                  </svg>
                  <span>
                    {contact.address}
                    {contact.city ? `, ${contact.city}` : ""}
                  </span>
                </div>
              ) : null}
              {contact.email ? (
                <p className="text-sm text-muted">{contact.email}</p>
              ) : null}
            </div>

            {/* Social — each icon appears only if this tenant published that
                link. Leaving them hardcoded would have pointed every company's
                customers at one specific business's accounts. */}
            <div className="mt-6 flex gap-3">
              {contact.facebook_url ? (
              <a
                href={contact.facebook_url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex h-9 w-9 items-center justify-center rounded-full border border-bd-border bg-surface text-muted transition hover:border-bd-border hover:text-foreground"
                aria-label="Facebook"
              >
                <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/>
                </svg>
              </a>
              ) : null}
              {contact.instagram_url ? (
              <a
                href={contact.instagram_url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex h-9 w-9 items-center justify-center rounded-full border border-bd-border bg-surface text-muted transition hover:border-bd-border hover:text-foreground"
                aria-label="Instagram"
              >
                <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838c-3.403 0-6.162 2.759-6.162 6.162s2.759 6.163 6.162 6.163 6.162-2.759 6.162-6.163c0-3.403-2.759-6.162-6.162-6.162zm0 10.162c-2.209 0-4-1.79-4-4 0-2.209 1.791-4 4-4s4 1.791 4 4c0 2.21-1.791 4-4 4zm6.406-11.845c-.796 0-1.441.645-1.441 1.44s.645 1.44 1.441 1.44c.795 0 1.439-.645 1.439-1.44s-.644-1.44-1.439-1.44z"/>
                </svg>
              </a>
              ) : null}
            </div>
          </div>

          {/* Navigation */}
          <div>
            <p className="text-[10px] font-bold uppercase tracking-[0.25em] text-muted">Tienda</p>
            <ul className="mt-4 space-y-3">
              {[
                { href: "/product", label: "Catálogo" },
                { href: "/product?cat=iphone", label: "iPhones" },
                { href: "/product?cat=accesorios", label: "Accesorios" },
                { href: "/product?cat=repuestos", label: "Repuestos" },
                { href: "/cart", label: "Mi Carrito" },
              ].map((link) => (
                <li key={link.href}>
                  <Link
                    href={link.href}
                    className="text-sm text-muted transition hover:text-foreground"
                  >
                    {link.label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>

          {/*
            UNA SOLA FUENTE.

            Aquí había una segunda lista de servicios, escrita a mano, más corta
            que la de `/services` y ya divergente: incluía «Diagnóstico
            Gratuito», que la otra no ofrecía con ese nombre y que ninguna
            política del proyecto respalda. Dos listas de lo mismo divergen
            siempre; quien las lee no sabe cuál es la buena.

            Ahora ambas salen de los servicios ACTIVOS del tenant. Un taller que
            deja de ofrecer algo lo desactiva una vez y desaparece de los dos
            sitios.
          */}
          {services.length > 0 ? (
          <div>
            <p className="text-[10px] font-bold uppercase tracking-[0.25em] text-muted">Servicios</p>
            <ul className="mt-4 space-y-3">
              {services.slice(0, 5).map((s) => (
                <li key={s.title}>
                  <Link
                    href="/services"
                    className="text-sm text-muted transition-colors hover:text-foreground"
                  >
                    {s.title}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
          ) : null}
        </div>

        {/* Bottom bar */}
        <div className="mt-12 flex flex-col items-center justify-between gap-4 border-t border-bd-border pt-8 sm:flex-row">
          <p className="text-xs text-muted">
            © {new Date().getFullYear()}
            {storeName ? ` ${storeName}.` : ""} Todos los derechos reservados.
          </p>
          {contact.website_url ? (
            <a
              href={contact.website_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-muted transition hover:text-muted"
            >
              {contact.website_url.replace(/^https?:\/\//, "")}
            </a>
          ) : null}
        </div>
      </div>
    </footer>
  );
}
