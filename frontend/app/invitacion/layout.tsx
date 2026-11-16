import type { Metadata } from "next";

/** SEO-01 — a private or per-person route: never indexed, never followed. */
export const metadata: Metadata = { robots: { index: false, follow: false } };

export default function NoIndexLayout({ children }: { children: React.ReactNode }) {
  return children;
}
