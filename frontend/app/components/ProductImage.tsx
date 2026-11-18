import Image from "next/image";
import { preload } from "react-dom";
import { isManagedStorefrontImage, storefrontMediaStyle } from "../lib/storefront-media";

/**
 * La foto de un producto, o cualquier imagen de la tienda que llena su caja
 * (`relative` con alto): también la del hero.
 *
 * LA DIRECCIÓN LA ESCRIBE LA TIENDA, y puede estar en cualquier host. El
 * optimizador de imágenes sólo admite los de `NEXT_PUBLIC_IMAGE_HOSTS`, y
 * `next/image` LANZA con cualquier otro: una foto pegada desde otro sitio
 * tumbaba la portada, el catálogo o el carrito enteros.
 *
 * Aquí se optimiza lo que se puede:
 *  · una foto de un host permitido;
 *  · una imagen subida desde el panel, con la ruta exacta que guarda el backend
 *    (`/api/storefront/images/<id>`). PERF-LCP: el hero de la portada se
 *    servía como un PNG de 1,3 MB sin `srcset`. El optimizador la pide POR
 *    DENTRO del propio servidor de Next (`app/api/[...path]` → Django por la red
 *    interna), sin salir a internet, y `images.localPatterns` no admite ninguna
 *    otra ruta local.
 * Lo demás —un host fuera de la lista, o una imagen del panel escrita como URL
 * absoluta o con consulta— se muestra con un `<img>` normal. Nunca rompe la
 * página.
 */

const OPTIMISABLE_HOSTS = (process.env.NEXT_PUBLIC_IMAGE_HOSTS ?? "")
  .split(",").map((host) => host.trim().toLowerCase()).filter(Boolean);

function matchesHost(hostname: string, pattern: string): boolean {
  if (pattern.startsWith("*.")) return hostname.endsWith(pattern.slice(1));
  return hostname === pattern;
}

/**
 * La ruta de una imagen del panel tal como la guarda el backend: sin barra
 * final ni consulta. Es la misma regla que `images.localPatterns` en
 * `next.config.ts`; en `next dev`, una ruta que no cabe en ella hace lanzar a
 * `next/image`.
 */
const OPTIMISABLE_MANAGED_PATH = /^\/api\/storefront\/images\/[0-9a-f]{32}$/;

export function canOptimise(src: string): boolean {
  if (OPTIMISABLE_MANAGED_PATH.test(src)) return true;
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
   * P2 · F2 — «this is the page's main picture» (its LCP): the V4 pages, the V3
   * hero and the V3 product page's main photo say it. A plain `<img>` is also
   * announced in the document's head, as `next/image` does with its own.
   */
  fetchPriority?: "high";
}) {
  if (canOptimise(src)) {
    return (
      <Image
        src={src}
        alt={alt}
        fill
        sizes={sizes}
        priority={priority}
        // The same `loading` as the plain `<img>` below: `next/image` leaves it
        // out when `priority` is set, and the markup should not depend on the
        // branch taken.
        loading={priority ? "eager" : undefined}
        fetchPriority={fetchPriority}
        style={storefrontMediaStyle(src)}
        className={className}
      />
    );
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
