import { SessionWatcher } from "../components/SessionWatcher";

/**
 * AUTH-C — every internal screen lives inside the session watcher: the warning
 * before the idle limit, and nothing internal left on display once it is over.
 */
export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return <SessionWatcher mode="internal">{children}</SessionWatcher>;
}
