/**
 * SEO-AUDIT #17 — la página 404 tiene título propio («Página no encontrada | <tienda>», con la plantilla del
 * layout raíz) en vez de sólo el nombre de la tienda. El noindex lo sigue poniendo Next en cada respuesta 404.
 */
import { metadata } from '@/app/not-found';

describe('not-found.tsx', () => {
  it('se titula «Página no encontrada»', () => {
    expect(metadata.title).toBe('Página no encontrada');
  });
});
