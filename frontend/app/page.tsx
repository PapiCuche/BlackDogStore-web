import type { Metadata } from "next";
import Home from "./home-client";
import { canonical } from "./lib/canonical";

// SEO-03: the storefront home is a client component; this server wrapper only adds its canonical
// (absolute, from the request host; none without a valid host — see lib/canonical).
export async function generateMetadata(): Promise<Metadata> {
  return { alternates: await canonical("/") };
}

export default function HomePage() {
  return <Home />;
}
