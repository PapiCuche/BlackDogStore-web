/**
 * SEO-03 — cada página pública estática declara su URL canónica (relativa: el origen lo pone `metadataBase`, que sale
 * del host de la petición). Sin ella, «/product?category=x» y la portada con parámetros competían como duplicados.
 */
import * as home from '@/app/page';
import * as catalog from '@/app/product/page';
import * as contact from '@/app/contact/page';
import * as about from '@/app/about/page';
import * as services from '@/app/services/page';
import * as privacy from '@/app/privacy/page';
import * as cookies from '@/app/cookies/page';
import * as whatsapp from '@/app/privacy/whatsapp/page';

type WithMeta = { metadata?: { title?: unknown; alternates?: { canonical?: unknown } | null } };

const pages: [string, WithMeta, string][] = [
  ['portada', home, '/'],
  ['catálogo', catalog, '/product'],
  ['contacto', contact, '/contact'],
  ['nosotros', about, '/about'],
  ['servicio técnico', services, '/services'],
  ['privacidad', privacy, '/privacy'],
  ['cookies', cookies, '/cookies'],
  ['privacidad de WhatsApp', whatsapp, '/privacy/whatsapp'],
];

describe('SEO-03 · canónicas de las páginas públicas', () => {
  it.each(pages)('%s declara su canónica relativa', (_label, mod, path) => {
    expect(mod.metadata?.alternates?.canonical).toBe(path);
  });

  it('el catálogo tiene título propio (antes salía sólo el nombre de la tienda)', () => {
    expect(catalog.metadata?.title).toBe('Productos');
  });

  it('ninguna canónica lleva un dominio fijo (multiempresa)', () => {
    for (const [, mod] of pages) expect(mod.metadata?.alternates?.canonical).toMatch(/^\//);
  });
});
