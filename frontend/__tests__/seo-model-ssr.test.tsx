/**
 * @jest-environment node
 */
import { renderToString } from 'react-dom/server';

import { StorefrontProvider } from '@/app/components/StorefrontProvider';
import type { CatalogModel } from '@/app/lib/catalog-models';
import { NEUTRAL_CONFIG } from '@/app/lib/storefront';

import { modelFixture, reply } from './support/device-commerce-fixtures';

/**
 * SEO-AUDIT #2 y #4 — la ficha V3 `/modelo/<slug>` llega pintada desde el servidor.
 *
 * Antes el primer HTML sólo traía la cabecera y el pie: el nombre (h1), el
 * precio, la foto y el JSON-LD llegaban después de una lectura en el
 * navegador. Ahora, como la V4 (P2 · F2), la página lee el modelo en el
 * servidor y se lo pasa a `ModelDetail` como primer dato; el navegador lo
 * vuelve a leer en silencio (precios y stock cambian mientras alguien mira).
 *
 *  · el HTML del servidor trae el h1, el precio de la elección inicial y la
 *    foto principal, sin esqueleto;
 *  · emite un `ProductGroup` (JSON-LD) con las direcciones V3, sólo con lo que
 *    dice la API: precio en soles, disponibilidad real, ninguna Offer para los
 *    seminuevos, ninguna marca ni valoración;
 *  · `generateMetadata` y la página leen el modelo UNA vez por petición;
 *  · «no existe» es `notFound()` (la página Y la metadata: el título pasa a
 *    ser el de `not-found.tsx`). El 404 REAL lo da el proxy: ver
 *    `seo-proxy-404.test.ts` y la nota de ese archivo sobre `app/loading.tsx`;
 *  · «falló la lectura» NO es «no existe»: la página queda en el estado que el
 *    navegador vuelve a intentar, y la metadata dice noindex.
 */

// Como en `storefront-v4-ssr.test.tsx`: un `cache()` que memoriza como el del servidor durante una petición.
const requestMemos: Map<string, unknown>[] = [];
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

// `jest.setup.ts` turns `notFound()` into a no-op for the whole suite; these pages need Next's own, which throws.
jest.mock('next/navigation', () => ({
  ...jest.requireActual<object>('next/navigation'),
  notFound: jest.requireActual<typeof import('next/dist/client/components/not-found')>('next/dist/client/components/not-found').notFound,
}));

let headerValues: Record<string, string> = {};
jest.mock('next/headers', () => ({
  headers: async () => ({ get: (name: string) => headerValues[name.toLowerCase()] ?? null }),
}));

const ORIGIN = 'https://tienda.example';
const NOT_FOUND = { digest: 'NEXT_HTTP_ERROR_FALLBACK;404' };

const fetchMock = jest.fn();
beforeEach(() => {
  for (const memo of requestMemos) memo.clear();
  headerValues = { host: 'tienda.example', 'x-forwarded-proto': 'https' };
  fetchMock.mockReset();
  global.fetch = fetchMock as unknown as typeof fetch;
});

/** The fixture at `slug`, with one variant «bajo pedido» so the three availabilities are covered. */
function modelAt(slug: string): CatalogModel {
  const model = { ...modelFixture(), slug } as unknown as CatalogModel;
  model.variants = model.variants.map((variant) => ({ ...variant }));
  Object.assign(model.variants[1], { available: true, availability: 'supplier', on_order: { lead_time_days: 7, price: '4199.00' } });
  return model;
}

function serve(model: Record<string, unknown> | null, status = 200) {
  fetchMock.mockImplementation(async (url: string) => {
    if (!String(url).includes('/catalog/models/')) return reply({}, 404);
    return model === null ? reply({}, status) : reply(model);
  });
}

async function page(slug: string, line?: string) {
  const { default: Page } = await import('@/app/modelo/[slug]/page');
  return Page({ params: Promise.resolve({ slug }), searchParams: Promise.resolve(line ? { line } : {}) });
}

async function metadata(slug: string) {
  const { generateMetadata } = await import('@/app/modelo/[slug]/page');
  return generateMetadata({ params: Promise.resolve({ slug }) });
}

async function serverHtml(slug: string, line?: string) {
  const element = await page(slug, line);
  // `<!-- -->` separa dos textos seguidos («S/ » y el importe); el navegador lo lee como uno.
  return renderToString(<StorefrontProvider config={NEUTRAL_CONFIG}>{element}</StorefrontProvider>)
    .replace(/<!-- -->/g, '');
}

function jsonLd(html: string): Record<string, unknown>[] {
  return [...html.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)]
    .flatMap((match) => {
      const block = JSON.parse(match[1]);
      return Array.isArray(block) ? block : [block];
    });
}

function modelCalls() {
  return fetchMock.mock.calls.filter(([url]) => String(url).includes('/catalog/models/'));
}

describe('el primer HTML de /modelo/<slug> (V3)', () => {
  it('trae el h1 con el nombre, el precio de la elección inicial y la foto principal, sin esqueleto', async () => {
    serve(modelAt('ssr-a'));
    const html = await serverHtml('ssr-a');
    expect(html).toMatch(/<h1[^>]*>Equipo X<\/h1>/);
    expect(html).toContain('S/ 3999.00');
    expect(html).toContain('http://localhost/azul-128.png');
    expect(html).not.toContain('Cargando modelo');
  });

  it('con ?line=used, los equipos seminuevos con su propio precio', async () => {
    serve(modelAt('ssr-b'));
    const html = await serverHtml('ssr-b', 'used');
    expect(html).toContain('S/ 2599.00');
  });

  it('pide el modelo a la API pública con el slug escapado y sin caché compartida entre hosts', async () => {
    serve(modelAt('a b'));
    await serverHtml('a b');
    const [url, init] = modelCalls()[0];
    expect(String(url)).toMatch(/\/catalog\/models\/a%20b$/);
    expect(init).toMatchObject({ cache: 'no-store' });
  });

  it('generateMetadata y la página comparten UNA lectura del modelo', async () => {
    serve(modelAt('ssr-once'));
    const meta = await metadata('ssr-once');
    await serverHtml('ssr-once');
    expect(meta.title).toBe('Equipo X');
    expect(modelCalls()).toHaveLength(1);
  });
});

describe('JSON-LD de la ficha V3', () => {
  it('un ProductGroup con la dirección V3, sus variantes nuevas en soles y la disponibilidad real', async () => {
    serve(modelAt('ld-a'));
    const blocks = jsonLd(await serverHtml('ld-a'));
    const group = blocks.find((block) => block['@type'] === 'ProductGroup')!;
    expect(group).toMatchObject({
      name: 'Equipo X', url: `${ORIGIN}/modelo/ld-a`, productGroupID: 'ld-a', description: 'Un equipo de prueba.',
    });
    const variants = group.hasVariant as Record<string, Record<string, unknown>>[];
    // Sólo las 5 variantes NUEVAS: los seminuevos nunca son variantes.
    expect(variants).toHaveLength(5);
    expect(variants[0].offers).toMatchObject({ price: '3999.00', priceCurrency: 'PEN', availability: 'https://schema.org/InStock' });
    expect(variants[1].offers).toMatchObject({ price: '4199.00', availability: 'https://schema.org/BackOrder' });
    expect(variants[3].offers).toMatchObject({ price: '4499.00', availability: 'https://schema.org/OutOfStock' });
    // La V3 no tiene una dirección por variante: todas apuntan a la ficha. Nada de /v4.
    for (const variant of variants) {
      expect(variant.url).toBe(`${ORIGIN}/modelo/ld-a`);
      expect(variant.offers.url).toBe(`${ORIGIN}/modelo/ld-a`);
    }
    expect(JSON.stringify(blocks)).not.toContain('/v4');
  });

  it('migas Inicio › Catálogo › Modelo con direcciones V3', async () => {
    serve(modelAt('ld-b'));
    const crumbs = jsonLd(await serverHtml('ld-b')).find((block) => block['@type'] === 'BreadcrumbList');
    expect(crumbs).toEqual({
      '@context': 'https://schema.org',
      '@type': 'BreadcrumbList',
      itemListElement: [
        { '@type': 'ListItem', position: 1, name: 'Inicio', item: `${ORIGIN}/` },
        { '@type': 'ListItem', position: 2, name: 'Catálogo', item: `${ORIGIN}/product` },
        { '@type': 'ListItem', position: 3, name: 'Equipo X', item: `${ORIGIN}/modelo/ld-b` },
      ],
    });
  });

  it('nada inventado: ni marca, ni valoración, ni Offer para un modelo sólo con seminuevos', async () => {
    const model = { ...modelAt('ld-used'), variants: [], lines: { new: { label: 'N', count: 0 }, used: { label: 'U', count: 2 } } };
    serve(model);
    const blocks = jsonLd(await serverHtml('ld-used'));
    const text = JSON.stringify(blocks);
    expect(text).not.toMatch(/"(brand|aggregateRating|review|offers)"/);
  });

  it('el texto es plano y un nombre con «</script>» no cierra el script', async () => {
    const name = 'Equipo </script><script>alert(1)</script>';
    serve({ ...modelAt('ld-esc'), name, description: '**Rápido** y _fiable_' });
    const html = await serverHtml('ld-esc');
    expect(html).not.toContain('<script>alert(1)');
    const group = jsonLd(html).find((block) => block['@type'] === 'ProductGroup')!;
    expect(group.name).toBe(name);
    expect(group.description).toBe('Rápido y fiable');
  });

  it('sin host válido no hay migas ni direcciones relativas', async () => {
    headerValues = { host: 'evil.example/<x>' };
    serve(modelAt('ld-nohost'));
    const blocks = jsonLd(await serverHtml('ld-nohost'));
    expect(blocks.find((block) => block['@type'] === 'BreadcrumbList')).toBeUndefined();
    expect(JSON.stringify(blocks)).not.toMatch(/"(url|item)":"\//);
  });
});

describe('un modelo que no existe (#4)', () => {
  it('la página llama a notFound()', async () => {
    serve(null, 404);
    await expect(page('no-existe')).rejects.toMatchObject(NOT_FOUND);
  });

  it('la metadata también: el título y el noindex son los de not-found.tsx', async () => {
    serve(null, 404);
    await expect(metadata('no-existe')).rejects.toMatchObject(NOT_FOUND);
  });
});

describe('si la lectura falla, no se dice «no encontrado» (#4)', () => {
  it.each([
    ['la API responde 500', () => serve(null, 500)],
    ['la red falla', () => fetchMock.mockRejectedValue(new Error('down'))],
  ])('%s: la página queda en el estado que el navegador reintenta, sin JSON-LD', async (_label, arrange) => {
    arrange();
    const html = await serverHtml('caido');
    expect(html).toContain('Cargando modelo');
    expect(html).not.toContain('no encontrado');
    expect(jsonLd(html)).toEqual([]);
  });

  it.each([
    ['la API responde 500', () => serve(null, 500)],
    ['la red falla', () => fetchMock.mockRejectedValue(new Error('down'))],
  ])('%s: la metadata no dice «no encontrado» y pide noindex', async (_label, arrange) => {
    arrange();
    const meta = await metadata('caido');
    expect(String(meta.title)).not.toMatch(/no encontrado/i);
    expect(meta.robots).toMatchObject({ index: false });
    expect(meta.alternates).toBeUndefined();
  });
});
