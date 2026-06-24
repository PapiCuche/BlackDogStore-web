// Client-side: requests go through the Next.js rewrite proxy (/api/* → Django) — no CORS.
// Server-side: direct connection to Django (Node.js to Node.js, no CORS needed).
const djangoApi =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://127.0.0.1:8000/api";

export const API_BASE =
  typeof window === "undefined"
    ? djangoApi
    : "/api";

/** Strips the trailing slash that is before a `?` (or at the end of a path).
 *  Prevents 308 redirects from the Next.js trailing-slash normalizer.
 *  /products/          → /products
 *  /products/?q=1      → /products?q=1
 *  /products?q=1       → /products?q=1  (unchanged)
 */
export function normalizeApiPath(path: string): string {
  const cleanPath = path.startsWith("/") ? path : `/${path}`;
  const qIdx = cleanPath.indexOf("?");
  const pathname = qIdx === -1 ? cleanPath : cleanPath.slice(0, qIdx);
  const query = qIdx === -1 ? "" : cleanPath.slice(qIdx);
  const normalized =
    pathname.length > 1 && pathname.endsWith("/")
      ? pathname.slice(0, -1)
      : pathname;
  return normalized + query;
}

export function apiUrl(path: string): string {
  return `${API_BASE}${normalizeApiPath(path)}`;
}

export async function fetcher<T>(url: string): Promise<T> {
  let res: Response;
  if (process.env.NODE_ENV === "development") {
    console.log("[fetcher] →", url);
  }
  try {
    res = await fetch(url);
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : String(cause);
    // console.warn (not error) — overlay only triggers on console.error in Turbopack dev mode
    console.warn("[fetcher] Network error", { url, message });
    throw new Error(
      `No se pudo conectar con el servidor (${message}). URL: ${url}`,
    );
  }
  if (!res.ok) {
    console.warn("[fetcher] HTTP error", { url, status: res.status, statusText: res.statusText });
    throw new Error(`Error del servidor: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

export function normalizeListResponse<T>(data: T[] | { results?: T[] }): T[] {
  if (Array.isArray(data)) return data;
  if (data && Array.isArray((data as { results?: T[] }).results)) {
    return (data as { results: T[] }).results;
  }
  return [];
}
