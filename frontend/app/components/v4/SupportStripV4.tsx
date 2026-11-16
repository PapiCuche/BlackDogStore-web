"use client";

/**
 * P2 · F1 — where to ask, next to the product (it used to be only in the
 * footer).
 *
 * Each item comes from the shop's own configuration, decided as the footer
 * decides it:
 *  - contact: WhatsApp when there is a number; otherwise e-mail; otherwise a
 *    phone. None of them, no contact item.
 *  - the warranty policy: only when the shop published a link.
 *  - repairs: only when the shop published services (a shop that does not
 *    repair does not say it does).
 * With nothing to show, the strip is not drawn.
 */

import Link from "next/link";
import { useStorefront } from "../StorefrontProvider";

export function SupportStripV4() {
  const { contact, policies, services } = useStorefront();
  const items: React.ReactNode[] = [];

  if (contact.whatsapp_link) {
    items.push(
      <a key="contact" href={contact.whatsapp_link} target="_blank" rel="noopener noreferrer" className="v4-link">
        Escríbenos por WhatsApp
      </a>,
    );
  } else if (contact.email) {
    items.push(
      <a key="contact" href={`mailto:${contact.email}`} className="v4-link">
        Escríbenos a {contact.email}
      </a>,
    );
  } else if (contact.phone) {
    items.push(
      <a key="contact" href={`tel:${contact.phone.replace(/[^\d+]/g, "")}`} className="v4-link">
        Llámanos al {contact.phone}
      </a>,
    );
  }

  if (policies.warranty_url?.trim()) {
    items.push(
      <a key="warranty" href={policies.warranty_url.trim()} className="v4-link">Política de garantía</a>,
    );
  }

  if (services.length > 0) {
    items.push(<Link key="repairs" href="/services" className="v4-link">Reparaciones</Link>);
  }

  if (items.length === 0) return null;

  return (
    <section aria-labelledby="v4-support-title" className="v4-support">
      <div className="mx-auto flex max-w-7xl flex-col gap-5 px-4 py-10 sm:px-6 lg:flex-row lg:items-center lg:justify-between lg:px-10">
        <h2 id="v4-support-title" className="v4-section-title text-base">¿Necesitas ayuda?</h2>
        <ul className="flex flex-wrap gap-x-8 gap-y-3 text-sm">
          {items.map((item, at) => <li key={at}>{item}</li>)}
        </ul>
      </div>
    </section>
  );
}

export default SupportStripV4;
