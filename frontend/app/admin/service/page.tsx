import { redirect } from "next/navigation";

/**
 * SVC-NAV-01. `/admin/service` was the only service screen; each stage now has
 * its own route. The old address stays valid — bookmarks, the dashboard and the
 * mobile deep links point here — and lands on the general list.
 */
export default function ServiceRootPage() {
  redirect("/admin/service/orders");
}
