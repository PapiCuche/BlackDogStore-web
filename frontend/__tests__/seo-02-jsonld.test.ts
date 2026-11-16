/**
 * SEO-02 — datos estructurados (JSON-LD) sólo con lo que la tienda tiene: nada inventado y nada que rompa el <script>.
 */
import {
  absoluteUrl, breadcrumbJsonLd, productJsonLd, serializeJsonLd, storeJsonLd,
} from '@/app/lib/structured-data';
import { NEUTRAL_CONFIG, type StorefrontConfig } from '@/app/lib/storefront';

const ORIGIN = 'https://tienda.example';

function config(overrides: Partial<StorefrontConfig['contact']> = {}, company: Partial<StorefrontConfig['company']> = {}): StorefrontConfig {
  return {
    ...NEUTRAL_CONFIG,
    company: { ...NEUTRAL_CONFIG.company, name: 'Tienda Demo', legal_name: 'DEMO S.A.C.', tax_id: '20000000001', ...company },
    branding: { ...NEUTRAL_CONFIG.branding, logo_url: '/media/logo.png' },
    contact: { ...NEUTRAL_CONFIG.contact, email: 'hola@tienda.example', phone: '+51 999 000 111', address: 'Av. Siempre Viva 1',
      city: 'Arequipa, Perú', facebook_url: 'https://facebook.com/demo', instagram_url: 'javascript:alert(1)', ...overrides },
  };
}

describe('Store', () => {
  it('usa sólo los datos configurados, con URL absolutas y sin redes que no sean https', () => {
    const store = storeJsonLd(config(), ORIGIN)!;
    expect(store).toMatchObject({
      '@type': 'Store', name: 'Tienda Demo', legalName: 'DEMO S.A.C.', taxID: '20000000001', url: ORIGIN,
      logo: `${ORIGIN}/media/logo.png`, email: 'hola@tienda.example', telephone: '+51 999 000 111',
      address: { '@type': 'PostalAddress', streetAddress: 'Av. Siempre Viva 1', addressLocality: 'Arequipa, Perú' },
      sameAs: ['https://facebook.com/demo'],
    });
    for (const invented of ['openingHours', 'geo', 'aggregateRating', 'review', 'priceRange']) expect(store).not.toHaveProperty(invented);
  });

  it('un campo vacío se omite; sin nombre de empresa no hay Store', () => {
    const store = storeJsonLd(config({ address: '', city: '', email: '' }), ORIGIN)!;
    expect(store).not.toHaveProperty('address');
    expect(store).not.toHaveProperty('email');
    expect(storeJsonLd(config({}, { name: '' }), ORIGIN)).toBeNull();
  });
});

describe('Product', () => {
  const base = { slug: 'funda-roja', name: 'Funda roja', price: '59.90', inventory: 3, image_url: '/media/f.png' };

  it('con stock: InStock, precio propio en soles y URL canónica', () => {
    const data = productJsonLd(base, ORIGIN, 'Funda de silicona');
    expect(data).toMatchObject({
      '@type': 'Product', name: 'Funda roja', description: 'Funda de silicona', image: [`${ORIGIN}/media/f.png`],
      url: `${ORIGIN}/product/funda-roja`,
      offers: { '@type': 'Offer', price: '59.90', priceCurrency: 'PEN', availability: 'https://schema.org/InStock' },
    });
    expect(data).not.toHaveProperty('aggregateRating');
  });

  it('sin stock: BackOrder si se vende bajo pedido, OutOfStock si no', () => {
    // Review: the «bajo pedido» price the page shows, never the shelf price.
    expect(productJsonLd({ ...base, inventory: 0, on_order: { price: '70.00' } }, ORIGIN).offers)
      .toMatchObject({ availability: 'https://schema.org/BackOrder', price: '70.00' });
    expect(productJsonLd({ ...base, price: '0.00', inventory: 0, on_order: { price: '70.00' } }, ORIGIN).offers)
      .toMatchObject({ price: '70.00' });
    expect(productJsonLd({ ...base, inventory: 0, on_order: null }, ORIGIN).offers)
      .toMatchObject({ availability: 'https://schema.org/OutOfStock' });
  });

  it('un seminuevo no lleva Offer: cada equipo tiene su precio', () => {
    expect(productJsonLd({ ...base, condition_line: 'used' }, ORIGIN)).not.toHaveProperty('offers');
  });

  it('sin precio no hay Offer: nunca un precio inventado', () => {
    expect(productJsonLd({ ...base, price: '0.00' }, ORIGIN)).not.toHaveProperty('offers');
    expect(productJsonLd({ ...base, price: 'x' }, ORIGIN)).not.toHaveProperty('offers');
  });
});

describe('Breadcrumb y escape', () => {
  it('migas absolutas; sin origen no hay lista', () => {
    expect(breadcrumbJsonLd([{ name: 'Inicio', path: '/' }, { name: 'Funda', path: '/product/funda' }], ORIGIN))
      .toMatchObject({ itemListElement: [{ position: 1, item: `${ORIGIN}/` }, { position: 2, item: `${ORIGIN}/product/funda` }] });
    expect(breadcrumbJsonLd([{ name: 'Inicio', path: '/' }], null)).toBeNull();
  });

  it('un nombre con </script> no cierra el script', () => {
    const text = serializeJsonLd({ name: '</script><script>alert(1)</script>&' });
    expect(text).not.toMatch(/<|>|&/);
    expect(JSON.parse(text)).toEqual({ name: '</script><script>alert(1)</script>&' });
  });

  it('una URL que no es https ni relativa se descarta', () => {
    expect(absoluteUrl('http://otro.example/x.png', ORIGIN)).toBe('');
    expect(absoluteUrl('//otro.example/x.png', ORIGIN)).toBe('');
    expect(absoluteUrl('/media/x.png', null)).toBe('');
  });
});
