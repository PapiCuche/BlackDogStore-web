"use client";

/**
 * NAV-SALES-01 — the query of the address, for a screen that the menu opens with a filter.
 *
 * `/admin/orders?supply=pending` and `/admin/orders` are the same route: Next keeps the page mounted when only the
 * query changes, so a screen that reads its filter once (when it mounts) misses the next one. This calls `onChange`
 * with the query the first time and on every change after, never on a plain re-render. It brings its own Suspense
 * boundary, which `useSearchParams` needs on a statically built page.
 */

import { Suspense, useEffect, useRef } from "react";
import { useSearchParams } from "next/navigation";

type Props = { onChange: (query: string) => void };

function Listener({ onChange }: Props) {
  const query = useSearchParams()?.toString() ?? "";
  const latest = useRef(onChange);
  useEffect(() => {
    latest.current = onChange;
  });
  useEffect(() => {
    latest.current(query);
  }, [query]);
  return null;
}

export function AddressQuery({ onChange }: Props) {
  return (
    <Suspense fallback={null}>
      <Listener onChange={onChange} />
    </Suspense>
  );
}
