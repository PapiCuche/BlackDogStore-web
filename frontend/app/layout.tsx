import type { Metadata } from "next";
import { Montserrat } from "next/font/google";
import "./globals.css";
import { AppChrome } from "./components/AppChrome";
import { StorefrontProvider } from "./components/StorefrontProvider";
import { brandingStyle, fetchStorefrontConfig } from "./lib/storefront";

const montserrat = Montserrat({
  variable: "--font-montserrat",
  weight: ["400", "500", "600", "700", "900"],
  style: ["normal", "italic"],
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
      className={`${montserrat.variable} h-full antialiased`}
      style={theme}
    >
      <body className="min-h-full bg-background text-foreground font-sans">
        <StorefrontProvider config={config}>
          <AppChrome>{children}</AppChrome>
        </StorefrontProvider>
      </body>
    </html>
  );
}
