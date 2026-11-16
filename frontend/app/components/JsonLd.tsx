import { serializeJsonLd } from "../lib/structured-data";

/**
 * SEO-02 — one `<script type="application/ld+json">`. The content is our own JSON, escaped by `serializeJsonLd`
 * (no «<» survives), so the inline script can never be closed early by a product name or a description.
 */
export function JsonLd({ data }: { data: Record<string, unknown> | Record<string, unknown>[] | null }) {
  if (!data || (Array.isArray(data) && data.length === 0)) return null;
  return <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: serializeJsonLd(data) }} />;
}
