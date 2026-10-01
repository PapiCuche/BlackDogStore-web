"use client";

import Link from "next/link";
import { useStorefront } from "./StorefrontProvider";

const WHATSAPP_ICON = (
  <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z" />
  </svg>
);

export function Footer() {
  const { company, branding, contact, policies } = useStorefront();
  const storeName = company.name;
  const logo = branding.logo_url;
  const hasContact = Boolean(contact.whatsapp_link || contact.email || contact.phone);

  return (
    <footer className="border-t border-bd-border bg-background">
      <div className="mx-auto max-w-7xl px-6 py-12 lg:px-8 lg:py-16">
        <div className="grid gap-10 border-b border-bd-border pb-10 lg:grid-cols-[1.2fr_0.8fr_0.8fr]">
          <div className="max-w-xl">
            <div className="flex items-center gap-4">
              {logo ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={logo} alt="" className="h-10 max-w-40 object-contain" />
              ) : null}
              <div className="min-w-0">
                <p className="truncate font-display text-lg font-extrabold uppercase tracking-[-0.02em] text-foreground">
                  {storeName || "Tienda"}
                </p>
                {contact.city ? (
                  <p className="mt-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted">
                    {contact.city}
                  </p>
                ) : null}
              </div>
            </div>

            <p className="mt-6 max-w-md text-sm leading-7 text-muted">
              Catálogo, servicio técnico y contacto de la empresa reunidos en un solo lugar.
            </p>

            {hasContact ? (
              <div className="mt-6 flex flex-wrap gap-3">
                {contact.whatsapp_link ? (
                  <a
                    href={contact.whatsapp_link}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-2 rounded-xl border border-bd-border px-4 py-2.5 text-xs font-bold text-foreground transition hover:border-foreground/25"
                  >
                    {WHATSAPP_ICON}
                    WhatsApp
                  </a>
                ) : null}
                {contact.email ? (
                  <a
                    href={`mailto:${contact.email}`}
                    className="inline-flex items-center rounded-xl border border-bd-border px-4 py-2.5 text-xs font-bold text-foreground transition hover:border-foreground/25"
                  >
                    Correo
                  </a>
                ) : null}
              </div>
            ) : null}

            <div className="mt-6 space-y-2 text-sm text-muted">
              {contact.phone ? <p>{contact.phone}</p> : null}
              {contact.address ? (
                <p>
                  {contact.address}
                  {contact.city ? `, ${contact.city}` : ""}
                </p>
              ) : null}
            </div>
          </div>

          <nav aria-label="Navegación del pie">
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-muted">Tienda</p>
            <ul className="mt-4 space-y-3">
              {[
                { href: "/product", label: "Catálogo" },
                { href: "/product?category=iphone", label: "iPhone" },
                { href: "/product?category=accesorios", label: "Accesorios" },
                { href: "/services", label: "Servicio técnico" },
                { href: "/cart", label: "Carrito" },
              ].map((link) => (
                <li key={link.href}>
                  <Link href={link.href} className="text-sm text-muted transition hover:text-foreground">
                    {link.label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>

          <div>
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-muted">Información</p>
            <ul className="mt-4 space-y-3">
              {policies.warranty_url ? (
                <li>
                  <a href={policies.warranty_url} target="_blank" rel="noopener noreferrer" className="text-sm text-muted transition hover:text-foreground">
                    Garantía
                  </a>
                </li>
              ) : null}
              {policies.terms_url ? (
                <li>
                  <a href={policies.terms_url} target="_blank" rel="noopener noreferrer" className="text-sm text-muted transition hover:text-foreground">
                    Términos
                  </a>
                </li>
              ) : null}
              {policies.privacy_url ? (
                <li>
                  <a href={policies.privacy_url} target="_blank" rel="noopener noreferrer" className="text-sm text-muted transition hover:text-foreground">
                    Privacidad
                  </a>
                </li>
              ) : null}
              {contact.website_url ? (
                <li>
                  <a href={contact.website_url} target="_blank" rel="noopener noreferrer" className="text-sm text-muted transition hover:text-foreground">
                    Sitio web
                  </a>
                </li>
              ) : null}
            </ul>

            {(contact.facebook_url || contact.instagram_url) ? (
              <div className="mt-6 flex gap-4 text-xs font-bold uppercase tracking-[0.08em] text-muted">
                {contact.facebook_url ? (
                  <a href={contact.facebook_url} target="_blank" rel="noopener noreferrer" className="transition hover:text-foreground">
                    Facebook
                  </a>
                ) : null}
                {contact.instagram_url ? (
                  <a href={contact.instagram_url} target="_blank" rel="noopener noreferrer" className="transition hover:text-foreground">
                    Instagram
                  </a>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>

        <div className="flex flex-col gap-3 pt-6 text-xs text-muted sm:flex-row sm:items-center sm:justify-between">
          <p>
            © {new Date().getFullYear()}
            {storeName ? ` ${storeName}.` : ""} Todos los derechos reservados.
          </p>
          {company.legal_name ? <p>{company.legal_name}</p> : null}
        </div>
      </div>
    </footer>
  );
}
