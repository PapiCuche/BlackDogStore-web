const clientBase = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000/api";
const serverBase = process.env.API_BASE || process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000/api";

export const API_BASE = typeof window === "undefined" ? serverBase : clientBase;

export async function fetcher<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Failed to fetch ${url}: ${res.statusText}`);
  }
  return res.json();
}
