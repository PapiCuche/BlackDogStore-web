import type { Metadata } from "next";
import { siteOrigin } from "./site-origin";

/**
 * SEO-03 — the canonical address of a public page, ABSOLUTE and built from the request host (multi-tenant: never a
 * fixed domain). Without a valid host there is no canonical at all: a relative one with no `metadataBase` resolves to
 * «http://localhost:3000/…», which is worse than none (review of SEO-03).
 */
export async function canonical(path: string): Promise<Metadata["alternates"] | undefined> {
  const origin = await siteOrigin();
  return origin ? { canonical: `${origin}${path}` } : undefined;
}
