import type { Metadata } from "next";
import Home from "./home-client";

// SEO-03: the storefront home is a client component; this server wrapper only adds its canonical
// (relative: `metadataBase` comes from the request host, never a fixed domain).
export const metadata: Metadata = { alternates: { canonical: "/" } };

export default function HomePage() {
  return <Home />;
}
