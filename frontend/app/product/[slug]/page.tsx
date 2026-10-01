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
      <div className="flex min-h-[60vh] items-center justify-center bg-background px-6">
        <div className="max-w-md rounded-2xl border border-dashed border-bd-border px-6 py-12 text-center">
          <p className="font-display text-2xl font-extrabold uppercase text-foreground">Producto no encontrado</p>
          <p className="mt-2 text-sm leading-6 text-muted">El producto no está disponible o la dirección ya no corresponde a un artículo publicado.</p>
          <Link href="/product" className="mt-6 inline-flex rounded-xl border border-bd-border px-5 py-3 text-xs font-bold uppercase tracking-[0.06em] text-foreground transition hover:border-foreground/25">
            Ver catálogo
          </Link>
        </div>
      </div>
    );
  }

  return <ProductDetail product={product} />;
}
