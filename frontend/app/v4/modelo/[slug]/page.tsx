import type { Metadata } from "next";
import { ModelConfiguratorV4 } from "../../../components/v4/ModelConfiguratorV4";
import { apiUrl } from "../../../lib/api";

/**
 * P2 · F1 — the model page in the V4 preview.
 *
 * Same data and same behaviour as `/modelo/<slug>` (V3); only the design
 * differs. Never indexed, whatever the model: the preview is not the shop's
 * public page.
 */

type Props = {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ line?: string | string[] }>;
};

const NOINDEX = { index: false, follow: false } as const;

export async function generateMetadata({ params }: Pick<Props, "params">): Promise<Metadata> {
  const { slug } = await params;
  try {
    const res = await fetch(apiUrl(`/catalog/models/${encodeURIComponent(slug)}/`), { cache: "no-store" });
    if (!res.ok) return { title: "Modelo no encontrado", robots: NOINDEX };
    const model = await res.json();
    return { title: typeof model?.name === "string" && model.name ? model.name : "Modelo", robots: NOINDEX };
  } catch {
    return { title: "Modelo", robots: NOINDEX };
  }
}

export default async function ModelPageV4({ params, searchParams }: Props) {
  const { slug } = await params;
  const { line } = await searchParams;
  return <ModelConfiguratorV4 slug={slug} initialLine={typeof line === "string" ? line : undefined} />;
}
