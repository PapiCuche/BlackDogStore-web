import type { Metadata } from "next";
import CatalogPage from "./catalog-client";
import { canonical } from "../lib/canonical";

// SEO-03: one canonical for the catalogue whatever its filters («?category=…», «?q=…»), and its own title.
export async function generateMetadata(): Promise<Metadata> {
  return { title: "Productos", alternates: await canonical("/product") };
}

export default function ProductCatalogPage() {
  return <CatalogPage />;
}
