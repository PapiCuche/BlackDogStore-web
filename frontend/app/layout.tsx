import type { Metadata } from "next";
import { Inter, Unbounded } from "next/font/google";
import "./globals.css";
import {
  StorefrontFooter,
  StorefrontHeader,
  WhatsAppButton,
} from "./components/StorefrontChrome";
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

  /*
    `es-PE`, no `es` a secas. El navegador formatea `<input type="date">` según
    el idioma del documento, y con «es» los filtros de fecha del panel pedían
    `mm/dd/yyyy` — formato estadounidense en una tienda peruana, que además se
    lee mal justo los doce primeros días de cada mes.
  */
  return (
    <html
      lang="es-PE"
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
          {/*
            El armazón del ESCAPARATE, y sólo donde hay escaparate. `/admin`
            tiene el suyo — barra lateral, selector de empresa, barra superior —
            y llevaba los dos puestos a la vez.
          */}
          <StorefrontHeader />
          {/*
            SIN `pt-16`. El header es `sticky`, no `fixed`: participa en el
            flujo y ya ocupa su propio alto. El padding lo compensaba una
            segunda vez y dejaba una franja vacía entre la cabecera y el hero.
          */}
          <div>{children}</div>
          <StorefrontFooter />
          <WhatsAppButton />
        </StorefrontProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
