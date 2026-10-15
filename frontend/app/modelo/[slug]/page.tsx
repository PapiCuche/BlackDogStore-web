import type { Metadata } from "next";
import ModelDetail from "../../components/ModelDetail";
import { apiUrl } from "../../lib/api";

/**
 * DC2 — the page of a model (its variants and its used devices).
 *
 * The metadata is read on the server; the page itself loads in the browser,
 * because what it shows — prices, availability, which used device is still
 * there — changes while somebody looks at it and is re-read after a refusal.
 */

type Props = {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ line?: string | string[] }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  try {
    const res = await fetch(apiUrl(`/catalog/models/${encodeURIComponent(slug)}/`), { cache: "no-store" });
    if (!res.ok) return { title: "Modelo no encontrado" };
    const model = await res.json();
    // The shop's name is appended by the root layout's title template.
    return {
      title: model.name,
      description: model.description || `Elige la opción de ${model.name} que prefieras.`,
      openGraph: {
        title: model.name,
        description: model.description,
        ...(model.image_url ? { images: [{ url: model.image_url }] } : {}),
      },
    };
  } catch {
    return { title: "Modelo" };
  }
}

export default async function ModelPage({ params, searchParams }: Props) {
  const { slug } = await params;
  const { line } = await searchParams;
  return <ModelDetail slug={slug} initialLine={typeof line === "string" ? line : undefined} />;
}
