import type { Metadata } from "next";
import { Inter, Unbounded } from "next/font/google";
import "./globals.css";
import { Header } from "./components/Header";
import { Footer } from "./components/Footer";
import { StorefrontProvider } from "./components/StorefrontProvider";
import { ThemeProvider, THEME_INIT_SCRIPT } from "./components/ThemeProvider";
import { brandingStyle, fetchStorefrontConfig } from "./lib/storefront";

const inter = Inter({
  variable: "--font-body",
  subsets: ["latin"],
  display: "swap",
});

const unbounded = Unbounded({
  variable: "--font-display",
  weight: ["400", "700", "800", "900"],
  subsets: ["latin"],
  display: "swap",
});

/**
 * Title, description and OpenGraph, from the TENANT that owns this host.
 *
 * The previous static object named one business and described its speciality,
 * which meant every tenant's storefront was announced to search engines and
 * social previews under somebody else's brand.
 *
 * This makes the root layout dynamic, and that is not a regression: a page whose
 * content depends on the request host CANNOT be prerendered once, and a static
 * prerender would be the bug — one company's title served to every domain.
 *
 * When nothing resolves, the metadata carries no brand name rather than a
 * borrowed one. A generic title is a visible gap; a wrong one is not.
 */
export async function generateMetadata(): Promise<Metadata> {
  const config = await fetchStorefrontConfig();
  const name = config.company.name;

  if (!name) {
    return { title: "Tienda", description: "" };
  }

  const description =
    config.policies.warranty_text ||
    `Compra en ${name}${config.contact.city ? ` · ${config.contact.city}` : ""}.`;

  return {
    title: { default: name, template: `%s | ${name}` },
    description,
    openGraph: {
      title: name,
      description,
      locale: "es_PE",
      type: "website",
      ...(config.branding.logo_url ? { images: [config.branding.logo_url] } : {}),
    },
    // FAVICON stays platform-level. Serving a per-tenant icon needs either an
    // upload pipeline or a dynamic icon route, and neither exists — see
    // docs/saas-multiempresa.md, "PENDIENTE — favicon por empresa".
    icons: { icon: "/assets/branding/favicon.svg" },
  };
}

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const config = await fetchStorefrontConfig();
  // Only `--brand-*` names with a `#RRGGBB` value survive brandingStyle(); the
  // backend validated them on the way in, and this is the last gate before they
  // reach a style attribute.
  const theme = brandingStyle(config);

  return (
    <html
      lang="es"
      className={`${inter.variable} ${unbounded.variable} h-full antialiased`}
      style={theme}
      // El script de abajo escribe `data-theme` y `style.colorScheme` antes de
      // que React hidrate, así que el servidor y el cliente difieren aquí a
      // propósito. Sin esto, React avisa de un desajuste que no es un error.
      suppressHydrationWarning
    >
      <head>
        {/*
          ANTES DEL PRIMER PAINT. Resolver el tema en un `useEffect` significa
          pintar una vez con el equivocado y corregir después: es el parpadeo
          blanco que se ve al recargar en oscuro.

          Único `dangerouslySetInnerHTML` del proyecto, y es legítimo porque el
          contenido es una constante del código — no llega por la red ni lo
          escribe una persona.
        */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="min-h-full bg-background text-foreground font-sans">
        <ThemeProvider>
        <StorefrontProvider config={config}>
          <Header />
          {/*
            SIN `pt-16`. El header es `sticky`, no `fixed`: participa en el
            flujo y ya ocupa su propio alto. El padding lo compensaba una
            segunda vez y dejaba una franja vacía —negra en tema oscuro— entre
            la cabecera y el hero.
          */}
          <div>{children}</div>
          <Footer />

        {/* WhatsApp floating button — only when this tenant published a number.
            Rendering it unconditionally would have sent every company's
            customers to one specific business's WhatsApp. */}
        {config.contact.whatsapp_link ? (
          <a
            href={config.contact.whatsapp_link}
            target="_blank"
            rel="noopener noreferrer"
            aria-label="Contactar por WhatsApp"
            className="fixed bottom-6 right-6 z-50 flex h-14 w-14 items-center justify-center rounded-full bg-[#25D366] shadow-2xl shadow-green-500/30 transition-transform hover:scale-110"
          >
          <svg className="h-7 w-7 text-foreground" fill="currentColor" viewBox="0 0 24 24">
            <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z" />
            </svg>
          </a>
        ) : null}
        </StorefrontProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
