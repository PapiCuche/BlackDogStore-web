"use client";

import Link from "next/link";
import { useStorefront, useStoreName } from "./StorefrontProvider";
import { BrandLogo } from "./BrandLogo";
import { StoreLink } from "./StoreLink";

/**
 * «Nosotros» y «Contacto»: la misma información, con distinto énfasis.
 *
 * TODO VIENE DE LA TIENDA. Nombre, dirección, teléfono, redes, garantía y
 * servicios salen de `useStorefront()`. Lo único escrito aquí son rótulos
 * («Dirección», «Teléfono») y titulares que valen para cualquier negocio. Un
 * dato que la tienda no publicó no se muestra, y si no publicó ninguno se dice
 * — no se rellena con los de otra.
 */
export function StoreInformation({ contactPage = false }: { contactPage?: boolean }) {
  const { contact, policies, services } = useStorefront();
  const storeName = useStoreName();
  const location = [contact.address, contact.city].filter(Boolean).join(", ");
  const hasContact = Boolean(location || contact.phone || contact.email || contact.whatsapp_link);
  const hasPolicies = Boolean(
    policies.warranty_text || policies.warranty_url || policies.terms_url || policies.privacy_url,
  );

  return (
    <main className="bg-background text-foreground">
      <section className="border-b border-bd-border bg-surface py-16 lg:py-24">
        <div className="v3-container grid items-center gap-10 lg:grid-cols-2">
          <div className="min-w-0">
            <p className="section-label">{contactPage ? "Contacto" : "Nosotros"}</p>
            <h1 className="v3-heading font-display mt-4 font-black uppercase tracking-tight text-balance">
              {contactPage ? `Escríbele a ${storeName}` : `Conoce ${storeName}`}
            </h1>
            <p className="mt-6 max-w-xl text-base leading-7 text-muted text-pretty">
              {contactPage
                ? "Estos son los canales que la tienda publicó para atenderte."
                : "Dónde encontrarnos, cómo escribirnos y las condiciones con las que trabajamos."}
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              {contact.whatsapp_link ? (
                <a
                  href={contact.whatsapp_link}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex min-h-11 items-center rounded-full bg-foreground px-7 py-3 text-sm font-bold uppercase tracking-widest text-background transition hover:opacity-90"
                >
                  Escribir por WhatsApp
                </a>
              ) : null}
              <Link
                href="/product"
                className="inline-flex min-h-11 items-center rounded-full border border-bd-border px-7 py-3 text-sm font-bold uppercase tracking-widest text-foreground transition hover:border-foreground/40"
              >
                Ver catálogo
              </Link>
            </div>
          </div>
          <div className="flex min-h-56 items-center justify-center rounded-2xl border border-bd-border bg-background p-10">
            <BrandLogo
              placement="hero"
              surface="theme"
              className="h-auto max-h-48 w-full max-w-sm object-contain"
              wordmarkClassName="font-display text-3xl font-black uppercase tracking-tight text-foreground"
            />
          </div>
        </div>
      </section>

      <section className="v3-container grid gap-10 py-16 lg:grid-cols-2">
        <div className="min-w-0">
          <h2 className="font-display text-2xl font-black uppercase tracking-tight">Encuéntranos</h2>
          {hasContact ? (
            <dl className="mt-8 space-y-6">
              {location ? (
                <div>
                  <dt className="text-xs font-semibold uppercase tracking-widest text-muted">Dirección</dt>
                  <dd className="mt-2 text-lg">{location}</dd>
                  <dd>
                    <StoreLink
                      href={`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(location)}`}
                      className="v3-text-link underline-offset-4 hover:underline"
                    >
                      Cómo llegar <span aria-hidden="true">↗</span>
                    </StoreLink>
                  </dd>
                </div>
              ) : null}
              {contact.phone ? (
                <div>
                  <dt className="text-xs font-semibold uppercase tracking-widest text-muted">Teléfono</dt>
                  <dd>
                    <a href={`tel:${contact.phone.replace(/\s+/g, "")}`} className="v3-text-link underline-offset-4 hover:underline">
                      {contact.phone}
                    </a>
                  </dd>
                </div>
              ) : null}
              {contact.email ? (
                <div>
                  <dt className="text-xs font-semibold uppercase tracking-widest text-muted">Correo</dt>
                  <dd className="break-all">
                    <a href={`mailto:${contact.email}`} className="v3-text-link underline-offset-4 hover:underline">
                      {contact.email}
                    </a>
                  </dd>
                </div>
              ) : null}
            </dl>
          ) : (
            <p className="mt-8 text-muted">La tienda todavía no ha publicado sus datos de contacto.</p>
          )}
          {contact.instagram_url || contact.facebook_url ? (
            <div className="mt-6 flex flex-wrap gap-6">
              {contact.instagram_url ? (
                <StoreLink href={contact.instagram_url} className="v3-text-link underline-offset-4 hover:underline">
                  Instagram <span aria-hidden="true">↗</span>
                </StoreLink>
              ) : null}
              {contact.facebook_url ? (
                <StoreLink href={contact.facebook_url} className="v3-text-link underline-offset-4 hover:underline">
                  Facebook <span aria-hidden="true">↗</span>
                </StoreLink>
              ) : null}
            </div>
          ) : null}
        </div>

        {hasPolicies ? (
          <div className="rounded-2xl border border-bd-border bg-surface p-6 sm:p-8">
            <h2 className="font-display text-2xl font-black uppercase tracking-tight">Condiciones</h2>
            {/* El texto de garantía es el de la tienda. Sin él no se escribe
                ninguno: prometer una garantía no es decisión de la plataforma. */}
            {policies.warranty_text ? (
              <p className="mt-4 leading-7 text-muted text-pretty">{policies.warranty_text}</p>
            ) : null}
            <ul className="mt-6 space-y-1">
              {policies.warranty_url ? (
                <li><StoreLink href={policies.warranty_url} className="v3-text-link underline-offset-4 hover:underline">Garantía <span aria-hidden="true">↗</span></StoreLink></li>
              ) : null}
              {policies.terms_url ? (
                <li><StoreLink href={policies.terms_url} className="v3-text-link underline-offset-4 hover:underline">Términos y condiciones <span aria-hidden="true">↗</span></StoreLink></li>
              ) : null}
              {policies.privacy_url ? (
                <li><StoreLink href={policies.privacy_url} className="v3-text-link underline-offset-4 hover:underline">Privacidad <span aria-hidden="true">↗</span></StoreLink></li>
              ) : null}
            </ul>
          </div>
        ) : null}
      </section>

      {!contactPage && services.length > 0 ? (
        <section className="border-t border-bd-border bg-surface py-16">
          <div className="v3-container">
            <h2 className="font-display text-2xl font-black uppercase tracking-tight">Servicios</h2>
            <ul className="mt-8 grid gap-4 md:grid-cols-2">
              {services.slice(0, 6).map((service) => (
                <li key={service.title} className="v3-category">
                  <p className="font-semibold text-foreground">{service.title}</p>
                  {service.description ? (
                    <p className="mt-2 text-sm leading-6 text-muted text-pretty">{service.description}</p>
                  ) : null}
                </li>
              ))}
            </ul>
            <Link href="/services" className="v3-text-link mt-6 underline-offset-4 hover:underline">
              Ver todos los servicios <span aria-hidden="true">→</span>
            </Link>
          </div>
        </section>
      ) : null}
    </main>
  );
}
