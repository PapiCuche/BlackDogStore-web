/**
 * @jest-environment node
 */
import { renderToString } from 'react-dom/server';

import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import type { CatalogModel } from '@/app/lib/catalog-models';
import { NEUTRAL_CONFIG } from '@/app/lib/storefront';
import { modelGroupJsonLd, modelListJsonLd, v4ModelPath } from '@/app/lib/structured-data';

import { modelFixture, reply } from './support/device-commerce-fixtures';

/**
 * V4-SEO — datos estructurados (JSON-LD) y OpenGraph de las páginas V4.
 *
 * Sólo cuando la empresa eligió la V4 en su panel: entonces `/v4` y
 * `/v4/modelo/<slug>` son las páginas públicas de la tienda y se indexan. Con
 * la V3 (lo de siempre) son una vista previa sin indexar y no emiten JSON-LD:
 * nada de señales duplicadas de una página que no debe estar en el buscador.
 *
 * Todo sale de los datos reales: el modelo es un `ProductGroup`; cada variante
 * NUEVA es un `Product` con su `Offer` en soles y su disponibilidad real
 * (stock propio → InStock; bajo pedido → BackOrder al precio bajo pedido; nada →
 * OutOfStock). Los seminuevos no llevan Offer: cada equipo tiene su precio
 * (como SEO-02). Ninguna marca, valoración ni reseña inventada.
 */

// Como en `storefront-v4-ssr.test.tsx`: un `cache()` que memoriza como el del servidor durante una petición.
// Cada prueba es una petición: su memoria se vacía antes de cada una (`resetRequestCache`).
const requestMemos: Map<string, unknown>[] = [];
function resetRequestCache() {
  for (const memo of requestMemos) memo.clear();
}
jest.mock('react', () => {
  const actual = jest.requireActual('react');
  return {
    ...actual,
    cache: <A extends unknown[], R>(fn: (...args: A) => R) => {
      const memo = new Map<string, R>();
      requestMemos.push(memo as Map<string, unknown>);
      return (...args: A) => {
        const key = JSON.stringify(args);
        if (!memo.has(key)) memo.set(key, fn(...args));
        return memo.get(key) as R;
      };
    },
  };
});

let headerValues: Record<string, string> = {};
jest.mock('next/headers', () => ({
  headers: async () => ({ get: (name: string) => headerValues[name.toLowerCase()] ?? null }),
}));

const ORIGIN = 'https://tienda.example';

// The fixture types `kind` as a plain string; it is the public model's shape.
type Model = CatalogModel;

/** The fixture with prices and availabilities that cover the three cases, and a relative photo. */
function modelWithSupply(slug: string): Model {
  const model = { ...modelFixture(), slug, image_url: '/media/modelo.png' } as unknown as Model;
  model.variants = model.variants.map((variant) => ({ ...variant }));
  // 41: own stock. 42: on order (bajo pedido) at its own price. 44: nothing to sell.
  Object.assign(model.variants[1], { available: true, availability: 'supplier', on_order: { lead_time_days: 7, price: '4199.00' } });
  return model;
}

const fetchMock = jest.fn();

/** The API of a shop in `design` with `model` (at its own slug) and a list of two models. */
function shop(design: string, model: Record<string, unknown>) {
  fetchMock.mockImplementation(async (url: string) => {
    const u = String(url);
    if (u.includes('/storefront/config')) return reply({ page: { storefront_design: design } });
    if (u.includes(`/catalog/models/${model.slug}`)) return reply(model);
    if (u.match(/\/catalog\/models\/?$/)) {
      return reply({
        lines: { new: { label: 'Nuevos', count: 2 }, used: { label: 'Usados', count: 0 } },
        results: [
          { slug: 'equipo-x', name: 'Equipo X', image_url: '', category: null, from_price: '3999.00', has_new: true, has_used: true },
          { slug: 'equipo-y', name: 'Equipo <Y>', image_url: '', category: null, from_price: null, has_new: true, has_used: false, sold_out: true },
        ],
      });
    }
    return reply({}, 404);
  });
}

beforeEach(() => {
  resetRequestCache();
  headerValues = { host: 'tienda.example', 'x-forwarded-proto': 'https' };
  fetchMock.mockReset();
  global.fetch = fetchMock as unknown as typeof fetch;
});

function jsonLdBlocks(html: string): unknown[] {
  return [...html.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)].map((m) => JSON.parse(m[1]));
}

function flat(blocks: unknown[]): Record<string, unknown>[] {
  return blocks.flatMap((block) => (Array.isArray(block) ? block : [block])) as Record<string, unknown>[];
}

async function modelHtml(slug: string) {
  const { default: Page } = await import('@/app/v4/modelo/[slug]/page');
  const element = await Page({ params: Promise.resolve({ slug }), searchParams: Promise.resolve({}) });
  return renderToString(<StorefrontProvider config={NEUTRAL_CONFIG}>{element}</StorefrontProvider>);
}

async function homeHtml() {
  const { default: Page } = await import('@/app/v4/page');
  const element = await Page();
  return renderToString(<StorefrontProvider config={NEUTRAL_CONFIG}>{element}</StorefrontProvider>);
}

// ---------------------------------------------------------------------------
// Los ayudantes
// ---------------------------------------------------------------------------

describe('modelGroupJsonLd', () => {
  const group = () => modelGroupJsonLd(modelWithSupply('equipo-x'), ORIGIN, 'Un equipo de prueba.');
  const variants = () => group().hasVariant as Record<string, Record<string, unknown>>[];

  it('el modelo es un ProductGroup con su nombre, su texto, su foto absoluta y su dirección V4', () => {
    expect(group()).toMatchObject({
      '@context': 'https://schema.org',
      '@type': 'ProductGroup',
      name: 'Equipo X',
      description: 'Un equipo de prueba.',
      image: [`${ORIGIN}/media/modelo.png`],
      url: `${ORIGIN}/v4/modelo/equipo-x`,
      productGroupID: 'equipo-x',
      variesBy: ['https://schema.org/color'],
    });
  });

  it('cada variante nueva es un Product con su Offer en soles y su disponibilidad real', () => {
    const [stock, onOrder, , none] = variants();
    expect(stock).toMatchObject({
      '@type': 'Product', name: 'Equipo X 41', sku: 'equipo-x-41', color: 'Azul',
      offers: {
        '@type': 'Offer', price: '3999.00', priceCurrency: 'PEN', availability: 'https://schema.org/InStock',
        itemCondition: 'https://schema.org/NewCondition', url: `${ORIGIN}/v4/modelo/equipo-x`,
      },
    });
    // Bajo pedido: el precio bajo pedido que muestra la página, nunca el de estante.
    expect(onOrder.offers).toMatchObject({ price: '4199.00', availability: 'https://schema.org/BackOrder' });
    expect(none.offers).toMatchObject({ price: '4499.00', availability: 'https://schema.org/OutOfStock' });
    expect(variants()).toHaveLength(5);
  });

  it('la foto de una variante es absoluta; una dirección que no es https ni relativa se descarta', () => {
    const model = modelWithSupply('equipo-x');
    model.variants[0].image_url = '/media/azul.png';
    const [first, second] = modelGroupJsonLd(model, ORIGIN).hasVariant as Record<string, unknown>[];
    expect(first.image).toEqual([`${ORIGIN}/media/azul.png`]);
    // «http://localhost/…» (la del fixture) no se publica como imagen.
    expect(second).not.toHaveProperty('image');
  });

  it('los seminuevos no llevan Offer ni aparecen como variante', () => {
    const text = JSON.stringify(group());
    expect(text).not.toContain('2599.00');
    expect(text).not.toContain('2899.00');
    expect(text).not.toContain('UsedCondition');
  });

  it('un modelo sólo con seminuevos no tiene ninguna Offer', () => {
    const model = { ...modelWithSupply('equipo-x'), variants: [] };
    const data = modelGroupJsonLd(model, ORIGIN);
    expect(data).not.toHaveProperty('hasVariant');
    expect(JSON.stringify(data)).not.toContain('Offer');
  });

  it('una variante sin precio no lleva Offer: nunca un precio inventado', () => {
    const model = modelWithSupply('equipo-x');
    model.variants[0].price = '0.00';
    expect((modelGroupJsonLd(model, ORIGIN).hasVariant as Record<string, unknown>[])[0]).not.toHaveProperty('offers');
  });

  it('sin atributo de color no hay variesBy ni color', () => {
    const model = modelWithSupply('equipo-x');
    model.attributes = model.attributes.filter((attribute) => attribute.kind !== 'color');
    const data = modelGroupJsonLd(model, ORIGIN);
    expect(data).not.toHaveProperty('variesBy');
    expect(JSON.stringify(data)).not.toContain('"color"');
  });

  it('ninguna marca, valoración ni reseña inventada', () => {
    const text = JSON.stringify(group());
    for (const invented of ['brand', 'Brand', 'Apple', 'aggregateRating', 'review', 'ratingValue']) {
      expect(text).not.toContain(invented);
    }
  });

  it('sin origen no hay direcciones relativas', () => {
    const data = modelGroupJsonLd(modelWithSupply('equipo-x'), null);
    expect(data).not.toHaveProperty('url');
    expect(data).not.toHaveProperty('image');
    expect(JSON.stringify(data)).not.toMatch(/"url":"\//);
  });
});

describe('modelListJsonLd', () => {
  it('un ItemList con la dirección V4 absoluta de cada modelo, en orden', () => {
    expect(modelListJsonLd([{ slug: 'equipo-x', name: 'Equipo X' }, { slug: 'equipo y', name: 'Equipo Y' }], ORIGIN)).toEqual({
      '@context': 'https://schema.org',
      '@type': 'ItemList',
      itemListElement: [
        { '@type': 'ListItem', position: 1, name: 'Equipo X', url: `${ORIGIN}/v4/modelo/equipo-x` },
        { '@type': 'ListItem', position: 2, name: 'Equipo Y', url: `${ORIGIN}/v4/modelo/equipo%20y` },
      ],
    });
  });

  it('sin origen o sin modelos no hay lista', () => {
    expect(modelListJsonLd([{ slug: 'equipo-x', name: 'Equipo X' }], null)).toBeNull();
    expect(modelListJsonLd([], ORIGIN)).toBeNull();
  });

  it('v4ModelPath escapa el slug', () => {
    expect(v4ModelPath('a/b')).toBe('/v4/modelo/a%2Fb');
  });
});

// ---------------------------------------------------------------------------
// Las páginas
// ---------------------------------------------------------------------------

describe('/v4/modelo/<slug>', () => {
  it('con V3 no emite JSON-LD: es una vista previa sin indexar', async () => {
    shop('v3', modelWithSupply('ld-v3'));
    expect(jsonLdBlocks(await modelHtml('ld-v3'))).toEqual([]);
  });

  it('con V4 emite el ProductGroup y las migas Inicio › Catálogo › Modelo con direcciones V4', async () => {
    shop('v4', modelWithSupply('ld-v4'));
    const blocks = flat(jsonLdBlocks(await modelHtml('ld-v4')));
    const group = blocks.find((block) => block['@type'] === 'ProductGroup')!;
    expect(group).toMatchObject({ name: 'Equipo X', url: `${ORIGIN}/v4/modelo/ld-v4`, description: 'Un equipo de prueba.' });
    expect((group.hasVariant as unknown[]).length).toBe(5);
    expect(blocks.find((block) => block['@type'] === 'BreadcrumbList')).toEqual({
      '@context': 'https://schema.org',
      '@type': 'BreadcrumbList',
      itemListElement: [
        { '@type': 'ListItem', position: 1, name: 'Inicio', item: `${ORIGIN}/v4` },
        { '@type': 'ListItem', position: 2, name: 'Catálogo', item: `${ORIGIN}/v4#modelos` },
        { '@type': 'ListItem', position: 3, name: 'Equipo X', item: `${ORIGIN}/v4/modelo/ld-v4` },
      ],
    });
  });

  it('el texto es plano: sin marcas de Markdown', async () => {
    shop('v4', { ...modelWithSupply('ld-md'), description: '**Rápido** y _fiable_' });
    const group = flat(jsonLdBlocks(await modelHtml('ld-md'))).find((block) => block['@type'] === 'ProductGroup')!;
    expect(group.description).toBe('Rápido y fiable');
  });

  it('un nombre con «</script>» no cierra el script antes de tiempo', async () => {
    const name = 'Equipo </script><script>alert(1)</script> & <b>';
    shop('v4', { ...modelWithSupply('ld-esc'), name });
    const html = await modelHtml('ld-esc');
    expect(html).not.toContain('<script>alert(1)');
    const group = flat(jsonLdBlocks(html)).find((block) => block['@type'] === 'ProductGroup')!;
    expect(group.name).toBe(name);
  });

  it('las direcciones salen del host de la petición; sin host válido no hay migas ni direcciones', async () => {
    headerValues = { host: 'otra.example', 'x-forwarded-proto': 'https' };
    shop('v4', modelWithSupply('ld-host'));
    let blocks = flat(jsonLdBlocks(await modelHtml('ld-host')));
    expect(blocks.find((block) => block['@type'] === 'ProductGroup')).toMatchObject({ url: 'https://otra.example/v4/modelo/ld-host' });

    headerValues = { host: 'evil.example/<x>' };
    shop('v4', modelWithSupply('ld-nohost'));
    blocks = flat(jsonLdBlocks(await modelHtml('ld-nohost')));
    expect(blocks.find((block) => block['@type'] === 'BreadcrumbList')).toBeUndefined();
    expect(JSON.stringify(blocks)).not.toMatch(/"(url|item)":"\//);
  });

  it('un modelo que no existe no emite JSON-LD', async () => {
    fetchMock.mockImplementation(async (url: string) => (String(url).includes('/storefront/config')
      ? reply({ page: { storefront_design: 'v4' } }) : reply({}, 404)));
    expect(jsonLdBlocks(await modelHtml('ld-missing'))).toEqual([]);
  });
});

describe('/v4 (portada)', () => {
  it('con V3 no emite JSON-LD', async () => {
    shop('v3', modelWithSupply('equipo-x'));
    expect(jsonLdBlocks(await homeHtml())).toEqual([]);
  });

  it('con V4 emite un ItemList de los modelos listados, con su dirección V4', async () => {
    shop('v4', modelWithSupply('equipo-x'));
    const html = await homeHtml();
    const list = flat(jsonLdBlocks(html)).find((block) => block['@type'] === 'ItemList')!;
    expect(list.itemListElement).toEqual([
      { '@type': 'ListItem', position: 1, name: 'Equipo X', url: `${ORIGIN}/v4/modelo/equipo-x` },
      { '@type': 'ListItem', position: 2, name: 'Equipo <Y>', url: `${ORIGIN}/v4/modelo/equipo-y` },
    ]);
    // El «<» del nombre va escapado dentro del script.
    expect(html).toContain('Equipo \\u003cY\\u003e');
  });

  it('si la lectura de modelos falla, no hay lista', async () => {
    fetchMock.mockImplementation(async (url: string) => (String(url).includes('/storefront/config')
      ? reply({ page: { storefront_design: 'v4' } }) : reply({}, 500)));
    expect(jsonLdBlocks(await homeHtml())).toEqual([]);
  });
});

describe('OpenGraph de la ficha V4', () => {
  it('con V4: título, texto, foto absoluta y la dirección canónica', async () => {
    shop('v4', modelWithSupply('og-v4'));
    const { generateMetadata } = await import('@/app/v4/modelo/[slug]/page');
    const meta = await generateMetadata({ params: Promise.resolve({ slug: 'og-v4' }) } as never);
    expect(meta.openGraph).toEqual({
      title: 'Equipo X',
      description: 'Un equipo de prueba.',
      url: `${ORIGIN}/v4/modelo/og-v4`,
      images: [{ url: `${ORIGIN}/media/modelo.png` }],
    });
  });

  it('una foto que no puede ser absoluta no se anuncia', async () => {
    shop('v4', { ...modelWithSupply('og-http'), image_url: 'http://localhost/modelo.png' });
    const { generateMetadata } = await import('@/app/v4/modelo/[slug]/page');
    const meta = await generateMetadata({ params: Promise.resolve({ slug: 'og-http' }) } as never);
    expect(meta.openGraph).not.toHaveProperty('images');
  });

  it('con V3 no hay OpenGraph', async () => {
    shop('v3', modelWithSupply('og-v3'));
    const { generateMetadata } = await import('@/app/v4/modelo/[slug]/page');
    const meta = await generateMetadata({ params: Promise.resolve({ slug: 'og-v3' }) } as never);
    expect(meta).not.toHaveProperty('openGraph');
  });
});
