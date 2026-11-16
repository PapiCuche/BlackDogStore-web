import type { Metadata } from "next";
import { SessionWatcher } from "../components/SessionWatcher";

/** SEO-01 — the internal panel is never indexed. */
export const metadata: Metadata = { robots: { index: false, follow: false } };

/**
 * AUTH-C — every internal screen lives inside the session watcher: the warning
 * before the idle limit, and nothing internal left on display once it is over.
 */
export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return <SessionWatcher mode="internal">{children}</SessionWatcher>;
}
