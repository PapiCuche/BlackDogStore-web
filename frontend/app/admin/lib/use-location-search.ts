"use client";

import { useEffect, useState } from "react";

/**
 * The query string of the address, read after each navigation. Not `useSearchParams`: that would need a Suspense
 * boundary around every admin page. `/admin/inventory/units?line=new` and the bare path are the same route, so the
 * menu and the tab bar need the query to tell which of them the person is on.
 */
export function useLocationSearch(pathname: string): [string, (next: string) => void] {
  const [search, setSearch] = useState("");
  useEffect(() => {
    const read = () => setSearch(typeof window === "undefined" ? "" : window.location.search);
    read();
    window.addEventListener("popstate", read);
    return () => window.removeEventListener("popstate", read);
  }, [pathname]);
  return [search, setSearch];
}
