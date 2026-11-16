import type { Metadata } from "next";
import CatalogPage from "./catalog-client";

// SEO-03: one canonical for the catalogue whatever its filters («?category=…», «?q=…»), and its own title.
export const metadata: Metadata = { title: "Productos", alternates: { canonical: "/product" } };

export default function ProductCatalogPage() {
  return <CatalogPage />;
}
