import Image from "next/image";
import { preload } from "react-dom";
import { isManagedStorefrontImage, storefrontMediaStyle } from "../lib/storefront-media";

/**
 * La foto de un producto. Llena la caja que la contiene (`relative` con alto).
 *
 * LA DIRECCIÓN LA ESCRIBE LA TIENDA, y puede estar en cualquier host. El
 * optimizador de imágenes sólo admite los de `NEXT_PUBLIC_IMAGE_HOSTS`, y
 * `next/image` LANZA con cualquier otro: una foto pegada desde otro sitio
 * tumbaba la portada, el catálogo o el carrito enteros.
 *
 * Aquí se optimiza lo que se puede. Lo demás —un host fuera de la lista, o una
 * imagen subida desde el panel, que ya viene acotada— se muestra con un `<img>`
 * normal. Nunca rompe la página.
 */

const OPTIMISABLE_HOSTS = (process.env.NEXT_PUBLIC_IMAGE_HOSTS ?? "")
  .split(",").map((host) => host.trim().toLowerCase()).filter(Boolean);

function matchesHost(hostname: string, pattern: string): boolean {
  if (pattern.startsWith("*.")) return hostname.endsWith(pattern.slice(1));
  return hostname === pattern;
}

export function canOptimise(src: string): boolean {
  if (isManagedStorefrontImage(src)) return false;
  try {
    const url = new URL(src);
    return url.protocol === "https:"
      && OPTIMISABLE_HOSTS.some((pattern) => matchesHost(url.hostname.toLowerCase(), pattern));
  } catch {
    return false;
  }
}

export function ProductImage({
  src, alt, sizes, className = "", priority = false, fetchPriority,
}: {
  src: string; alt: string; sizes: string; className?: string; priority?: boolean;
  /**
   * P2 · F2 — «this is the page's main picture» (its LCP). Only the V4 pages
   * say it; without it the markup is exactly the one before F2. A plain `<img>`
   * is also announced in the document's head, as `next/image` does with its own.
   */
  fetchPriority?: "high";
}) {
  if (canOptimise(src)) {
    return <Image src={src} alt={alt} fill sizes={sizes} priority={priority} fetchPriority={fetchPriority} className={className} />;
  }
  if (fetchPriority) preload(src, { as: "image", fetchPriority });
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={alt}
      loading={priority ? "eager" : "lazy"}
      fetchPriority={fetchPriority}
      decoding="async"
      style={storefrontMediaStyle(src)}
      className={`absolute inset-0 h-full w-full ${className}`}
    />
  );
}
