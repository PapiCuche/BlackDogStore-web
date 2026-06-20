import type { Metadata } from "next";
import Link from "next/link";
import ProductDetail from "../../components/ProductDetail";

type Props = {
  params: Promise<{ slug: string }>;
};

const API_BASE = process.env.API_BASE || process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000/api";

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const res = await fetch(`${API_BASE}/products/?slug=${slug}`, { cache: "no-store" });
  const data = await res.json();
  const product = data[0];

  if (!product) return { title: "Producto no encontrado" };

  return {
    title: product.name,
    description: product.description || `Compra ${product.name} en Black Dog Store Perú. Repuestos y equipos Apple originales con garantía.`,
    openGraph: {
      title: `${product.name} | Black Dog Store`,
      description: product.description,
      ...(product.image_url ? { images: [{ url: product.image_url }] } : {}),
    },
  };
}

export default async function ProductPage({ params }: Props) {
  const { slug } = await params;
  const res = await fetch(`${API_BASE}/products/?slug=${slug}`, { cache: "no-store" });
  const data = await res.json();
  const product = data[0];

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
