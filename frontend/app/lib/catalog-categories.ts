"use client";

import { useEffect, useState } from "react";
import { apiUrl, fetcher } from "./api";

export type CatalogCategory = { id: number; name: string; slug: string };

/**
 * The categories of THIS store's catalogue — the only list of categories the
 * storefront has.
 *
 * The header, the footer and the home each carried their own list, written by
 * hand for the first tenant (iPhone, Mac, iPad…). Another company saw links to
 * categories it does not sell; and the footer wrote `?cat=`, which the
 * catalogue does not read. Now there is one source, it is the server's, and
 * the order is the server's.
 *
 * One request per page load, shared by whoever asks. A failure is an empty
 * list: navigation without category shortcuts, never somebody else's.
 */
let pending: Promise<CatalogCategory[]> | null = null;

function loadCategories(): Promise<CatalogCategory[]> {
  if (!pending) {
    pending = fetcher<CatalogCategory[]>(apiUrl("/categories"))
      .then((rows) => (Array.isArray(rows) ? rows.filter((row) => row?.slug && row?.name) : []))
      .catch(() => {
        // Not cached: the next screen may find the server back.
        pending = null;
        return [];
      });
  }
  return pending;
}

export function useCatalogCategories(): CatalogCategory[] {
  const [categories, setCategories] = useState<CatalogCategory[]>([]);
  useEffect(() => {
    let current = true;
    void loadCategories().then((rows) => { if (current) setCategories(rows); });
    return () => { current = false; };
  }, []);
  return categories;
}

/** The catalogue filtered by a category, with the parameter the catalogue reads. */
export function categoryHref(slug: string): string {
  return `/product?category=${encodeURIComponent(slug)}`;
}

/** Tests only: forget the shared request. */
export function resetCatalogCategories() {
  pending = null;
}
