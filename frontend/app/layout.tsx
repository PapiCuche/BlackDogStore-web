import type { Metadata } from "next";
import { Inter, Montserrat } from "next/font/google";
import "./globals.css";
import { AppChrome } from "./components/AppChrome";
import { StorefrontProvider } from "./components/StorefrontProvider";
import { brandingStyle, fetchStorefrontConfig } from "./lib/storefront";

const inter = Inter({
  variable: "--font-neutral",
  subsets: ["latin"],
  display: "swap",
});

const montserrat = Montserrat({
  variable: "--font-brand",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800", "900"],
  style: ["normal", "italic"],
  display: "swap",
});

export async function generateMetadata(): Promise<Metadata> {
  const config = await fetchStorefrontConfig();
  const name = config.company.name;

  if (!name) {
    return { title: "Tienda", description: "" };
  }

  const description =
    `Catálogo y canales de atención de ${name}${config.contact.city ? ` en ${config.contact.city}` : ""}.`;

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
    ...(config.branding.logo_url ? { icons: { icon: config.branding.logo_url } } : {}),
  };
}

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const config = await fetchStorefrontConfig();
  const theme = brandingStyle(config);

  return (
    <html
      lang="es"
      className={`${inter.variable} ${montserrat.variable} h-full antialiased`}
      style={theme}
    >
      <body className="min-h-full bg-background font-sans text-foreground">
        <StorefrontProvider config={config}>
          <AppChrome whatsappLink={config.contact.whatsapp_link}>
            {children}
          </AppChrome>
        </StorefrontProvider>
      </body>
    </html>
  );
}
