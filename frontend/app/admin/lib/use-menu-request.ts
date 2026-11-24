"use client";

/**
 * NAV-SALES-01 — a menu entry that opens a screen on one of its parts (`/admin/sales/pos?panel=equipo`,
 * `/admin/products/models?seccion=colores`) is a REQUEST, not a state of the address:
 *
 *   - `onQuery` (given to `AddressQuery`) counts one request each time the address asks for the part;
 *   - `pending` is what was asked while a request has not been handled;
 *   - `done()` marks it handled and takes the parameter out of the address (`router.replace`, without scrolling), so
 *     clicking the same entry again changes the query and is a new request.
 *
 * The page keeps this, not the part that handles it: a part mounted again (a company switch remounts the till) finds
 * the request already handled and does not take the focus back.
 */

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";

type State<T> = { request: number; handled: number; value: T | null };

export function useMenuRequest<T>(read: (query: string) => T | null, basePath: string) {
  const router = useRouter();
  const [state, setState] = useState<State<T>>({ request: 0, handled: 0, value: null });

  const onQuery = useCallback((query: string) => {
    const value = read(query);
    if (value === null) return;
    setState((current) => ({ ...current, request: current.request + 1, value }));
  }, [read]);

  const done = useCallback(() => {
    setState((current) => (current.handled === current.request ? current : { ...current, handled: current.request }));
    router.replace(basePath, { scroll: false });
  }, [router, basePath]);

  return {
    pending: state.request > state.handled ? state.value : null,
    request: state.request,
    onQuery,
    done,
  };
}
