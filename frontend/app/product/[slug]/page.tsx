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
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-zinc-950">
        <p className="text-lg text-slate-400">Producto no encontrado.</p>
        <Link href="/product" className="text-sm text-zinc-400 hover:text-white underline transition">
          Ver catálogo completo
        </Link>
      </div>
    );
  }

  return <ProductDetail product={product} />;
}
