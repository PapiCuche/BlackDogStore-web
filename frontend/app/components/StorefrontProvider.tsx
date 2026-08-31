"use client";

/**
 * Makes the storefront's tenant config available to client components.
 *
 * The config is fetched on the SERVER in the root layout and passed down as a
 * prop — this provider only distributes it. Fetching it here instead would mean
 * every page first painted with a neutral theme and no shop name, then snapped
 * to the real one; and the metadata, which has to be right in the initial HTML,
 * could not use it at all.
 *
 * `useStorefront()` never returns null: components get the neutral config when
 * nothing resolved, so no consumer has to handle an absent shop.
 */

import { createContext, useContext } from "react";
import { NEUTRAL_CONFIG, type StorefrontConfig } from "../lib/storefront";

const StorefrontContext = createContext<StorefrontConfig>(NEUTRAL_CONFIG);

export function StorefrontProvider({
  config,
  children,
}: {
  config: StorefrontConfig;
  children: React.ReactNode;
}) {
  return (
    <StorefrontContext.Provider value={config}>{children}</StorefrontContext.Provider>
  );
}

export function useStorefront(): StorefrontConfig {
  return useContext(StorefrontContext);
}

/** The shop's name, or a neutral word — never another business's name. */
export function useStoreName(fallback = "la tienda"): string {
  const { company } = useStorefront();
  return company.name || fallback;
}
