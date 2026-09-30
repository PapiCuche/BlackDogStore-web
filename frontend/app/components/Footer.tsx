"use client";

import Link from "next/link";
import { useStorefront } from "./StorefrontProvider";

const WHATSAPP_SVG = (
  <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z" />
  </svg>
);

const storefrontLinks = [
  { href: "/product", label: "Catálogo" },
  { href: "/services", label: "Servicios" },
  { href: "/cart", label: "Carrito" },
  { href: "/orders", label: "Pedidos" },
  { href: "/auth", label: "Cuenta" },
];

export function Footer() {
  const { company, branding, contact, policies } = useStorefront();
  const storeName = company.name;
  const logo = branding.logo_url;

  const policyLinks = [
    policies.warranty_url ? { href: policies.warranty_url, label: "Garantía" } : null,
    policies.terms_url ? { href: policies.terms_url, label: "Términos" } : null,
    policies.privacy_url ? { href: policies.privacy_url, label: "Privacidad" } : null,
  ].filter((item): item is { href: string; label: string } => Boolean(item));

  return (
    <footer className="relative border-t border-bd-border bg-background">
      <div className="relative overflow-hidden border-b border-bd-border bg-surface">
        <div className="topo-bg pointer-events-none absolute inset-0 opacity-60" aria-hidden="true" />
        <div className="absolute left-0 top-0 h-px w-28 bg-accent" aria-hidden="true" />
        <div className="relative mx-auto flex max-w-7xl flex-col gap-6 px-6 py-10 sm:flex-row sm:items-end sm:justify-between lg:px-8">
          <div className="max-w-xl">
            <p className="text-[10px] font-bold uppercase tracking-[0.24em] text-accent">
              Atención
            </p>
            <p className="mt-3 font-display text-3xl font-black uppercase leading-tight text-foreground sm:text-4xl">
              ¿Necesitas ayuda con una compra o servicio?
            </p>
            <p className="mt-3 text-sm leading-6 text-muted-foreground">
              Usa el canal de contacto publicado por esta tienda para resolver disponibilidad,
              condiciones o seguimiento.
            </p>
          </div>

          {contact.whatsapp_link ? (
            <a
              href={contact.whatsapp_link}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex min-h-12 shrink-0 items-center justify-center gap-2 rounded-xl bg-primary px-6 py-3 text-xs font-bold uppercase tracking-[0.12em] text-background transition hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              {WHATSAPP_SVG}
              WhatsApp
            </a>
          ) : contact.email ? (
            <a
              href={"mailto:" + contact.email}
              className="inline-flex min-h-12 shrink-0 items-center justify-center rounded-xl bg-primary px-6 py-3 text-xs font-bold uppercase tracking-[0.12em] text-background transition hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              Escribir
            </a>
          ) : null}
        </div>
      </div>

      <div className="mx-auto max-w-7xl px-6 py-12 lg:px-8">
        <div className="grid gap-10 sm:grid-cols-2 lg:grid-cols-4">
          <div className="lg:col-span-2">
            <div className="flex items-center gap-4">
              {logo ? (
                <img src={logo} alt="" className="h-10 w-auto max-w-44 object-contain" />
              ) : null}
              <div className="min-w-0">
                <p className="truncate font-display text-lg font-black uppercase tracking-tight text-foreground">
                  {storeName || "Tienda"}
                </p>
                {contact.city ? (
                  <p className="mt-1 text-[10px] uppercase tracking-[0.2em] text-muted-foreground">
                    {contact.city}
                  </p>
                ) : null}
              </div>
            </div>

            <div className="mt-6 space-y-2.5 text-sm text-muted-foreground">
              {contact.phone ? <p>{contact.phone}</p> : null}
              {contact.email ? <p>{contact.email}</p> : null}
              {contact.address ? (
                <p>
                  {contact.address}
                  {contact.city ? ", " + contact.city : ""}
                </p>
              ) : null}
            </div>

            <div className="mt-6 flex gap-2">
              {contact.facebook_url ? (
                <a
                  href={contact.facebook_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex h-10 w-10 items-center justify-center rounded-lg border border-bd-border bg-surface text-muted-foreground transition hover:border-accent/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                  aria-label="Facebook"
                >
                  <span aria-hidden="true">f</span>
                </a>
              ) : null}
              {contact.instagram_url ? (
                <a
                  href={contact.instagram_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex h-10 w-10 items-center justify-center rounded-lg border border-bd-border bg-surface text-muted-foreground transition hover:border-accent/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                  aria-label="Instagram"
                >
                  <span aria-hidden="true">◎</span>
                </a>
              ) : null}
            </div>
          </div>

          <div>
            <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-muted-foreground">
              Navegación
            </p>
            <ul className="mt-4 space-y-3">
              {storefrontLinks.map((link) => (
                <li key={link.href}>
                  <Link href={link.href} className="text-sm text-muted-foreground transition hover:text-foreground">
                    {link.label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>

          <div>
            <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-muted-foreground">
              Información
            </p>
            <ul className="mt-4 space-y-3">
              {policyLinks.length > 0 ? (
                policyLinks.map((link) => (
                  <li key={link.href}>
                    <a
                      href={link.href}
                      className="text-sm text-muted-foreground transition hover:text-foreground"
                    >
                      {link.label}
                    </a>
                  </li>
                ))
              ) : (
                <li className="text-sm text-muted-foreground">
                  Consulta las condiciones antes de confirmar una compra o servicio.
                </li>
              )}
            </ul>
          </div>
        </div>

        <div className="mt-10 flex flex-col gap-3 border-t border-bd-border pt-6 text-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
          <p>
            © {new Date().getFullYear()}
            {storeName ? " " + storeName + "." : ""} Todos los derechos reservados.
          </p>
          {contact.website_url ? (
            <a
              href={contact.website_url}
              target="_blank"
              rel="noopener noreferrer"
              className="transition hover:text-foreground"
            >
              {contact.website_url.replace(/^https?:\/\//, "")}
            </a>
          ) : null}
        </div>
      </div>
    </footer>
  );
}
