/**
 * @jest-environment node
 */

import { hasLocalMatch } from 'next/dist/shared/lib/match-local-pattern';

import nextConfig from '@/next.config';
import { canOptimise } from '@/app/components/ProductImage';

/**
 * PERF-LCP — el optimizador sólo acepta, de este sitio, las imágenes subidas
 * desde el panel.
 *
 * Sin `images.localPatterns`, Next deja optimizar CUALQUIER ruta local:
 * `/_next/image?url=/api/<lo que sea>` hacía que el servidor pidiera por dentro
 * esa ruta de la API. Ahora la lista es explícita, y todo lo que `ProductImage`
 * manda al optimizador tiene que caber en ella; en `next dev` una ruta que no
 * cabe hace LANZAR a `next/image`.
 */

const patterns = nextConfig.images?.localPatterns;
const ID = '0123456789abcdef'.repeat(2);

describe('images.localPatterns', () => {
  it('está declarado: no se deja abierta cualquier ruta local', () => {
    expect(Array.isArray(patterns)).toBe(true);
    expect(patterns!.length).toBeGreaterThan(0);
  });

  it('admite la ruta de una imagen del panel tal como la guarda el backend', () => {
    expect(hasLocalMatch(patterns, `/api/storefront/images/${ID}`)).toBe(true);
  });

  it('rechaza el resto de la API, consultas añadidas y rutas que escapan', () => {
    for (const url of [
      '/api/storefront/config',
      '/api/admin/storefront/images',
      `/api/storefront/images/${ID}?w=1`,
      `/api/storefront/images/${ID}/../../admin`,
      '/assets/branding/logo.png',
    ]) {
      expect([url, hasLocalMatch(patterns, url)]).toEqual([url, false]);
    }
  });

  it('todo lo que ProductImage manda al optimizador cabe en la lista', () => {
    expect(canOptimise(`/api/storefront/images/${ID}`)).toBe(true);
    for (const src of [
      `/api/storefront/images/${ID}`,
      `/api/storefront/images/${ID}/`,
      `/api/storefront/images/${ID}?v=2`,
      `/api/storefront/images/${ID.toUpperCase()}`,
    ]) {
      if (canOptimise(src)) expect([src, hasLocalMatch(patterns, src)]).toEqual([src, true]);
    }
  });
});
