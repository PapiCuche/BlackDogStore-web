"use client";

import { openConsentPreferences } from "../lib/consent";
import { useStorefront, useStoreName } from "./StorefrontProvider";

/** Describes the measurement implementation; identity always belongs to this tenant. */
export function PrivacyNotice() {
  const { company, contact, policies } = useStorefront();
  const name = useStoreName();
  const address = [contact.address, contact.city].filter(Boolean).join(", ");
  return (
    <main className="bg-background text-foreground">
      <article className="v3-container max-w-3xl py-16 lg:py-24">
        <h1 className="font-display text-3xl font-bold">Privacidad y cookies</h1>
        <p className="mt-4 text-muted">Información sobre la medición de visitas y compras en {name}.</p>
        <div className="mt-10 space-y-8 text-sm leading-7 [&_h2]:mb-3 [&_h2]:text-xl [&_h2]:font-semibold [&_a]:underline [&_a]:underline-offset-4">
          <section>
            <h2>Responsable y contacto</h2>
            {company.legal_name ? <p>{company.legal_name}</p> : <p>La tienda todavía no ha publicado su identidad legal. Puedes solicitarla desde la página de contacto.</p>}
            {company.tax_id ? <p>Identificación fiscal: {company.tax_id}</p> : null}
            {address ? <p>{address}</p> : null}
            {contact.email ? <p>Consultas de privacidad: <a href={`mailto:${contact.email}`}>{contact.email}</a>.</p> : <p>Solicita información sobre privacidad a través de <a href="/contact">Contacto</a>.</p>}
          </section>
          <section>
            <h2>Tu decisión</h2>
            <p>Las cookies necesarias permiten la sesión, el carrito y las preferencias de apariencia. La analítica y el marketing son opcionales: permanecen desactivados hasta que los aceptas. Puedes rechazarlos sin dejar de comprar y aceptar una categoría sin aceptar la otra.</p>
            <p>La elección se guarda en este navegador, con su versión y fecha, y puede cambiarse desde «Preferencias de cookies». La elección de analítica y marketing al iniciar el pago se conserva con el pedido para decidir si se mide su compra.</p>
            <button type="button" onClick={openConsentPreferences} className="mt-4 min-h-11 rounded-xl border border-bd-border px-4 py-2 font-semibold hover:bg-surface">Cambiar preferencias de cookies</button>
          </section>
          <section>
            <h2>Google Analytics: visitas y compras</h2>
            <p>Si la integración está activa y aceptas analítica, enviamos a Google Analytics páginas visitadas, productos, búsquedas filtradas, acciones del carrito y del proceso de compra. Los eventos de compra incluyen la referencia del pedido, los productos, cantidades, precios, importe, impuestos y moneda, junto con identificadores de navegador y sesión. La finalidad es conocer el uso de la tienda y medir su embudo de compra.</p>
            <p>La compra puede enviarse desde el servidor cuando se confirma el pago, utilizando la elección guardada al iniciar el pago. Así puede medirse aunque no regreses a la tienda después de pagar. Esta integración no añade a los eventos de Google tu nombre, correo, teléfono, documento ni dirección de entrega.</p>
            <p>Google también procesa información técnica de las conexiones a su servicio. Los identificadores permiten relacionar visitas y compras; omitir tu nombre no convierte estos datos en anónimos. Consulta <a href="https://policies.google.com/technologies/partner-sites?hl=es" target="_blank" rel="noopener noreferrer">cómo Google utiliza los datos de sitios que usan sus servicios</a>.</p>
          </section>
          <section>
            <h2>Marketing opcional</h2>
            <p>Si Meta o TikTok están configurados y activos y aceptas marketing, sus herramientas pueden medir visitas, acciones y compras para conocer los resultados de la publicidad. Pueden recibir identificadores de publicidad y navegador, referencia del pedido, datos de productos e importe; las conversiones desde el servidor pueden incluir dirección IP y características del navegador. Rechazar marketing impide estos envíos en esta integración.</p>
            <p>Consulta las políticas de <a href="https://www.facebook.com/privacy/policy/" target="_blank" rel="noopener noreferrer">Meta</a> y <a href="https://www.tiktok.com/legal/page/row/privacy-policy/es" target="_blank" rel="noopener noreferrer">TikTok</a>. El tratamiento por estos proveedores puede realizarse fuera de tu país.</p>
          </section>
          <section>
            <h2>Conservación y cambios de preferencias</h2>
            <p>La preferencia permanece en este navegador hasta que la cambies, borres sus datos o se actualice la versión del aviso. Los identificadores guardados para conversiones desde el servidor se eliminan después de siete días; este plazo no elimina los datos del pedido ni los eventos ya recibidos por los proveedores. Las cookies de Google pueden durar hasta dos años; su duración y la conservación en Analytics dependen de la configuración del servicio.</p>
            <p>Retirar el consentimiento detiene la medición futura en este navegador. No borra automáticamente eventos ya enviados ni modifica por sí solo la elección de un pedido cuyo pago ya se inició. Para solicitar acceso, rectificación, cancelación, oposición o la revocación del tratamiento asociado a un pedido, contacta al responsable e indica tu solicitud.</p>
          </section>
          <section>
            <h2>Otros datos de la tienda</h2>
            <p>Este aviso describe cookies y medición. Los datos necesarios para cuentas, pedidos, pagos, comprobantes y servicio técnico se tratan para prestar esos servicios; rechazarlos no equivale a rechazar cookies opcionales.</p>
            {policies.privacy_url && !policies.privacy_url.endsWith('/privacy') ? <p>Consulta también la <a href={policies.privacy_url}>política de privacidad de la tienda</a> para las demás finalidades y plazos.</p> : <p>Puedes solicitar al responsable información sobre las demás finalidades, destinatarios y plazos de conservación de tus datos.</p>}
          </section>
        </div>
      </article>
    </main>
  );
}
