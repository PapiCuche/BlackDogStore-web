import type { Metadata } from "next";
import Link from "next/link";
import ProductDetail from "../../components/ProductDetail";
import { apiUrl } from "../../lib/api";

type Props = {
  params: Promise<{ slug: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  try {
    const res = await fetch(apiUrl(`/products?slug=${slug}`), { cache: "no-store" });
    if (!res.ok) return { title: "Producto no encontrado" };
    const data = await res.json();
    const product = data[0];
    if (!product) return { title: "Producto no encontrado" };
    // Phase 3: the shop's name is NOT repeated here. The root layout's title
    // template (`%s | <tenant>`) appends it, and it knows which tenant owns this
    // host; hardcoding one here would have branded every storefront's product
    // pages as the same business.
    return {
      title: product.name,
      description:
        product.description || `Compra ${product.name} en nuestra tienda.`,
      openGraph: {
        title: product.name,
        description: product.description,
        ...(product.image_url ? { images: [{ url: product.image_url }] } : {}),
      },
    };
  } catch {
    return { title: "Producto no encontrado" };
  }
}

export default async function ProductPage({ params }: Props) {
  const { slug } = await params;
  let product = null;
  try {
    const res = await fetch(apiUrl(`/products?slug=${slug}`), { cache: "no-store" });
    if (res.ok) {
      const data = await res.json();
      product = data[0] ?? null;
    }
  } catch {
    product = null;
  }

  if (!product) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background px-6 text-foreground">
        <div className="w-full max-w-lg rounded-2xl border border-bd-border bg-surface p-8 text-center">
          <span className="section-label">Catálogo</span>
          <h1 className="mt-2 font-display text-3xl font-black uppercase text-foreground">
            Producto no encontrado.
          </h1>
          <p className="mt-3 text-sm leading-6 text-muted-foreground">
            El producto puede no estar disponible o el enlace puede haber cambiado.
          </p>
          <Link
            href="/product"
            className="mt-7 inline-flex min-h-12 items-center rounded-xl bg-primary px-6 py-3 text-sm font-bold text-background transition hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            Ver catálogo
          </Link>
        </div>
      </div>
    );
  }

  return <ProductDetail product={product} />;
}
